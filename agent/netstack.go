// Pivx Agent - Plano de datos (Fase 2)
//
// Pila TCP/IP en userland basada en gVisor (netstack). Recibe paquetes IP crudos
// que llegan por el tunel WebSocket desde el servidor (que los captura de su
// interfaz TUN) y los "termina" localmente: por cada conexion TCP/UDP entrante
// abre un socket REAL hacia el destino en la red interna de la victima y hace de
// proxy en ambos sentidos. Las respuestas vuelven como paquetes IP hacia el tunel.
//
// Esto es lo que permite pivotar sin privilegios ni driver en el target: toda la
// "magia" de red ocurre en espacio de usuario. Mismo enfoque que Ligolo-ng.
//
// AVISO: la API de gVisor es sensible a la version. Este codigo se escribio contra
// la rama `go` de gVisor. Ver README (go get gvisor.dev/gvisor@go && go mod tidy).
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strconv"
	"sync"
	"syscall"
	"time"

	"gvisor.dev/gvisor/pkg/buffer"
	"gvisor.dev/gvisor/pkg/tcpip"
	"gvisor.dev/gvisor/pkg/tcpip/adapters/gonet"
	"gvisor.dev/gvisor/pkg/tcpip/header"
	"gvisor.dev/gvisor/pkg/tcpip/link/channel"
	"gvisor.dev/gvisor/pkg/tcpip/network/ipv4"
	"gvisor.dev/gvisor/pkg/tcpip/network/ipv6"
	"gvisor.dev/gvisor/pkg/tcpip/stack"
	"gvisor.dev/gvisor/pkg/tcpip/transport/icmp"
	"gvisor.dev/gvisor/pkg/tcpip/transport/tcp"
	"gvisor.dev/gvisor/pkg/tcpip/transport/udp"
	"gvisor.dev/gvisor/pkg/waiter"
)

const (
	nicID = 1
	// tunMTU DEBE coincidir con DEFAULT_MTU en server/pivx_server/tun.py. 1350
	// deja holgura para el overhead del framing WebSocket (y TLS/wss futuro) y
	// evita fragmentacion: el netstack emitira segmentos que caben en el tunel.
	tunMTU  = 1350
	dialTO  = 10 * time.Second
	udpIdle = 30 * time.Second
)

// NetStack encapsula la pila gVisor y el bombeo de paquetes hacia/desde el tunel.
type NetStack struct {
	stack        *stack.Stack
	ep           *channel.Endpoint
	ctx          context.Context
	cancel       context.CancelFunc
	sendToTunnel func([]byte) // encola un paquete IP crudo hacia el servidor
}

// NewNetStack construye la pila, registra los forwarders TCP/UDP y arranca el
// bucle que drena los paquetes de salida hacia el tunel.
func NewNetStack(sendToTunnel func([]byte)) (*NetStack, error) {
	s := stack.New(stack.Options{
		NetworkProtocols: []stack.NetworkProtocolFactory{
			ipv4.NewProtocol, ipv6.NewProtocol,
		},
		TransportProtocols: []stack.TransportProtocolFactory{
			tcp.NewProtocol, udp.NewProtocol,
			icmp.NewProtocol4, icmp.NewProtocol6,
		},
	})

	ep := channel.New(1024, tunMTU, "")
	if err := s.CreateNIC(nicID, ep); err != nil {
		return nil, fmt.Errorf("CreateNIC: %s", err)
	}

	// Aceptar paquetes para cualquier IP de destino (somos un router userland).
	s.SetPromiscuousMode(nicID, true)
	s.SetSpoofing(nicID, true)

	// Ruta por defecto: todo hacia nuestra NIC.
	s.SetRouteTable([]tcpip.Route{
		{Destination: header.IPv4EmptySubnet, NIC: nicID},
		{Destination: header.IPv6EmptySubnet, NIC: nicID},
	})

	// Deshabilitar SYN-Cookies: con cookies activas gVisor responde SYN-ACK a
	// TODO SYN sin crear estado, lo que confunde a nmap (todos los puertos
	// aparecen "open"). Sin ellas los escaneos SYN (-sS) dan resultados fieles.
	synCookies := tcpip.TCPAlwaysUseSynCookies(false)
	s.SetTransportProtocolOption(tcp.ProtocolNumber, &synCookies)

	ctx, cancel := context.WithCancel(context.Background())
	ns := &NetStack{
		stack:        s,
		ep:           ep,
		ctx:          ctx,
		cancel:       cancel,
		sendToTunnel: sendToTunnel,
	}

	ns.setupTCPForwarder()
	ns.setupUDPForwarder()
	go ns.outboundLoop()

	return ns, nil
}

// InjectInbound recibe un paquete IP crudo desde el tunel y lo inyecta en la pila.
// Los ICMP Echo Request se interceptan aqui para el smartping (verificacion real
// via OS antes de responder) en lugar de dejar que gVisor responda ciegamente.
func (ns *NetStack) InjectInbound(raw []byte) {
	if len(raw) == 0 {
		return
	}
	if isICMPEchoRequest(raw) {
		go ns.handleICMPEcho(raw)
		return
	}
	var proto tcpip.NetworkProtocolNumber
	switch raw[0] >> 4 {
	case 4:
		proto = header.IPv4ProtocolNumber
	case 6:
		proto = header.IPv6ProtocolNumber
	default:
		return
	}
	pkt := stack.NewPacketBuffer(stack.PacketBufferOptions{
		Payload: buffer.MakeWithData(raw),
	})
	ns.ep.InjectInbound(proto, pkt)
	pkt.DecRef()
}

// outboundLoop lee los paquetes que la pila quiere emitir (respuestas de las
// conexiones internas) y los envia por el tunel hacia el servidor/TUN.
func (ns *NetStack) outboundLoop() {
	for {
		pkt := ns.ep.ReadContext(ns.ctx)
		if pkt == nil {
			return // contexto cancelado
		}
		buf := pkt.ToBuffer()
		ns.sendToTunnel(buf.Flatten())
		pkt.DecRef()
	}
}

// Close libera la pila y detiene el bucle de salida.
func (ns *NetStack) Close() {
	ns.cancel()
	ns.ep.Close()
	ns.stack.Close()
}

// magicIPNet es el rango Class-E 240.0.0.0/4 (reservado). Las conexiones a IPs
// de este rango se redirigen a 127.0.0.1 del agente, permitiendo acceder a
// servicios que solo escuchan en localhost (DBs, APIs internas, paneles admin)
// sin conflictos de IP.
var magicIPNet = net.IPNet{
	IP:   net.IPv4(240, 0, 0, 0),
	Mask: net.CIDRMask(4, 32),
}

// rewriteMagicIP devuelve "127.0.0.1" si addr pertenece a 240.0.0.0/4;
// de lo contrario devuelve addr sin cambios.
func rewriteMagicIP(addr string) string {
	ip := net.ParseIP(addr)
	if ip != nil && magicIPNet.Contains(ip) {
		return "127.0.0.1"
	}
	return addr
}

// hostResponded indica si el error de Dial implica que el host remoto respondio
// activamente (connection refused = puerto cerrado). En ese caso el netstack
// debe devolver RST. Para timeouts/unreachable no se envia RST (equivale a
// "filtered" en nmap).
func hostResponded(err error) bool {
	var serr syscall.Errno
	if errors.As(err, &serr) {
		return serr == syscall.ECONNREFUSED
	}
	return false
}

// --- TCP ------------------------------------------------------------------

func (ns *NetStack) setupTCPForwarder() {
	fwd := tcp.NewForwarder(ns.stack, 0, 2048, func(r *tcp.ForwarderRequest) {
		id := r.ID()
		host := rewriteMagicIP(id.LocalAddress.String())
		dst := net.JoinHostPort(host, strconv.Itoa(int(id.LocalPort)))

		outbound, err := net.DialTimeout("tcp", dst, dialTO)
		if err != nil {
			rst := hostResponded(err)
			log.Printf("[tcp] destino inalcanzable %s: %v (RST=%v)", dst, err, rst)
			r.Complete(rst)
			return
		}

		var wq waiter.Queue
		gep, tcperr := r.CreateEndpoint(&wq)
		if tcperr != nil {
			outbound.Close()
			r.Complete(true)
			return
		}
		r.Complete(false)

		inbound := gonet.NewTCPConn(&wq, gep)
		log.Printf("[tcp] proxy establecido -> %s", dst)
		go pipe(inbound, outbound)
	})
	ns.stack.SetTransportProtocolHandler(tcp.ProtocolNumber, fwd.HandlePacket)
}

// --- UDP ------------------------------------------------------------------

func (ns *NetStack) setupUDPForwarder() {
	fwd := udp.NewForwarder(ns.stack, func(r *udp.ForwarderRequest) bool {
		id := r.ID()
		host := rewriteMagicIP(id.LocalAddress.String())
		dst := net.JoinHostPort(host, strconv.Itoa(int(id.LocalPort)))

		var wq waiter.Queue
		gep, err := r.CreateEndpoint(&wq)
		if err != nil {
			return true
		}
		inbound := gonet.NewUDPConn(&wq, gep)

		outbound, derr := net.Dial("udp", dst)
		if derr != nil {
			inbound.Close()
			return true
		}
		log.Printf("[udp] proxy establecido -> %s", dst)
		go ns.pipeUDPWithUnreachable(inbound, outbound, id)
		return true
	})
	ns.stack.SetTransportProtocolHandler(udp.ProtocolNumber, fwd.HandlePacket)
}

// pipeUDPWithUnreachable es como pipeUDP pero detecta ECONNREFUSED en el lado
// outbound (red real) e inyecta ICMP Port Unreachable de vuelta al tunel.
func (ns *NetStack) pipeUDPWithUnreachable(inbound, outbound net.Conn, id stack.TransportEndpointID) {
	defer inbound.Close()
	defer outbound.Close()
	done := make(chan struct{}, 2)

	// outbound -> inbound (respuestas de la red real)
	go func() {
		buf := make([]byte, 65535)
		for {
			_ = outbound.SetReadDeadline(time.Now().Add(udpIdle))
			n, err := outbound.Read(buf)
			if n > 0 {
				if _, werr := inbound.Write(buf[:n]); werr != nil {
					break
				}
			}
			if err != nil {
				if hostResponded(err) {
					ns.injectUDPPortUnreachable(id)
				}
				break
			}
		}
		done <- struct{}{}
	}()

	// inbound -> outbound (peticiones hacia la red real)
	go func() {
		buf := make([]byte, 65535)
		for {
			_ = inbound.SetReadDeadline(time.Now().Add(udpIdle))
			n, err := inbound.Read(buf)
			if n > 0 {
				if _, werr := outbound.Write(buf[:n]); werr != nil {
					if hostResponded(werr) {
						ns.injectUDPPortUnreachable(id)
					}
					break
				}
			}
			if err != nil {
				break
			}
		}
		done <- struct{}{}
	}()

	<-done
}

// --- helpers de proxy -----------------------------------------------------

// closeWriter es cualquier conexion que soporta cierre de solo-escritura
// (half-close). Lo cumplen *net.TCPConn y *gonet.TCPConn de gVisor.
type closeWriter interface {
	CloseWrite() error
}

// halfCloseWrite senala EOF al otro extremo sin destruir la conexion, de modo
// que el sentido contrario pueda seguir transportando datos. Si el tipo no lo
// soporta, se cae a un Close completo.
func halfCloseWrite(c net.Conn) {
	if cw, ok := c.(closeWriter); ok {
		_ = cw.CloseWrite()
		return
	}
	_ = c.Close()
}

// pipe copia bidireccionalmente entre dos conexiones orientadas a flujo (TCP).
//
// Cierre suave: cuando un sentido termina (EOF), solo se hace CloseWrite() en el
// destino de ESE sentido, dejando el sentido contrario intacto (half-open). Solo
// tras drenar AMBOS sentidos se cierran del todo las conexiones. Esto evita que
// herramientas de escaneo/servicios que hacen shutdown en una direccion vean su
// conexion cortada de golpe.
func pipe(a, b net.Conn) {
	defer a.Close()
	defer b.Close()
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		io.Copy(a, b) // b -> a
		halfCloseWrite(a)
	}()
	go func() {
		defer wg.Done()
		io.Copy(b, a) // a -> b
		halfCloseWrite(b)
	}()
	wg.Wait()
}

// pipeUDP copia datagramas con un timeout de inactividad (UDP no tiene EOF).
func pipeUDP(a, b net.Conn) {
	defer a.Close()
	defer b.Close()
	done := make(chan struct{}, 2)
	copyUDP := func(dst, src net.Conn) {
		buf := make([]byte, 65535)
		for {
			_ = src.SetReadDeadline(time.Now().Add(udpIdle))
			n, err := src.Read(buf)
			if n > 0 {
				if _, werr := dst.Write(buf[:n]); werr != nil {
					break
				}
			}
			if err != nil {
				break
			}
		}
		done <- struct{}{}
	}
	go copyUDP(a, b)
	go copyUDP(b, a)
	<-done
}
