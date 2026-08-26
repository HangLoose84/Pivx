package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"net"
	"os/exec"
	"runtime"

	"gvisor.dev/gvisor/pkg/tcpip/stack"
)

// isICMPEchoRequest comprueba si un paquete IP crudo es un ICMP Echo Request
// IPv4 (type 8). Otros tipos ICMP y todo IPv6 pasan directo al netstack.
func isICMPEchoRequest(raw []byte) bool {
	if len(raw) < 20 {
		return false
	}
	if raw[0]>>4 != 4 {
		return false
	}
	if raw[9] != 1 { // protocol = ICMP
		return false
	}
	ihl := int(raw[0]&0x0f) * 4
	if len(raw) < ihl+1 {
		return false
	}
	return raw[ihl] == 8 // type = Echo Request
}

// handleICMPEcho procesa un ICMP Echo Request a nivel de paquete IP crudo.
// Ejecuta un ping real desde el OS del agente (smartping) y, si el host esta
// vivo, construye un Echo Reply y lo inyecta de vuelta al tunel.
func (ns *NetStack) handleICMPEcho(raw []byte) {
	ihl := int(raw[0]&0x0f) * 4
	if len(raw) < ihl+8 {
		return
	}

	dstIP := ipToString(raw[16:20])

	targetIP := dstIP
	if rewriteMagicIP(dstIP) == "127.0.0.1" {
		targetIP = "127.0.0.1"
	}

	if !isHostAlive(targetIP) {
		log.Printf("[icmp] %s no responde", dstIP)
		return
	}

	log.Printf("[icmp] %s vivo -> Echo Reply", dstIP)

	reply := make([]byte, len(raw))
	copy(reply, raw)

	// Swap src <-> dst IP
	copy(reply[12:16], raw[16:20])
	copy(reply[16:20], raw[12:16])

	reply[8] = 64 // TTL

	// ICMP type = Echo Reply (0), code = 0
	reply[ihl] = 0
	reply[ihl+1] = 0

	// Recalcular checksum ICMP
	reply[ihl+2] = 0
	reply[ihl+3] = 0
	cksum := internetChecksum(reply[ihl:])
	binary.BigEndian.PutUint16(reply[ihl+2:ihl+4], cksum)

	// Recalcular checksum IP
	reply[10] = 0
	reply[11] = 0
	ipck := internetChecksum(reply[:ihl])
	binary.BigEndian.PutUint16(reply[10:12], ipck)

	ns.sendToTunnel(reply)
}

// isHostAlive ejecuta el ping del OS para verificar si un host esta activo.
// No requiere privilegios especiales en la mayoria de sistemas.
func isHostAlive(addr string) bool {
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.Command("ping", "-n", "1", "-w", "3000", addr)
	} else {
		cmd = exec.Command("ping", "-c", "1", "-W", "3", addr)
	}
	return cmd.Run() == nil
}

func ipToString(b []byte) string {
	return fmt.Sprintf("%d.%d.%d.%d", b[0], b[1], b[2], b[3])
}

// injectUDPPortUnreachable construye un paquete ICMP Destination Unreachable
// (Type 3, Code 3 = Port Unreachable) y lo envia por el tunel. Esto permite
// que nmap -sU distinga puertos cerrados (recibe ICMP) de filtrados (silencio).
func (ns *NetStack) injectUDPPortUnreachable(id stack.TransportEndpointID) {
	srcIP := net.ParseIP(id.LocalAddress.String()).To4()
	dstIP := net.ParseIP(id.RemoteAddress.String()).To4()
	if srcIP == nil || dstIP == nil {
		return
	}

	const (
		ipHL      = 20
		icmpHL    = 8 // type + code + checksum + unused
		origIPHL  = 20
		origUDPHL = 8
	)
	total := ipHL + icmpHL + origIPHL + origUDPHL
	pkt := make([]byte, total)

	// --- Outer IP header ---
	pkt[0] = 0x45 // v4, IHL=5
	binary.BigEndian.PutUint16(pkt[2:4], uint16(total))
	pkt[8] = 64  // TTL
	pkt[9] = 1   // protocol = ICMP
	copy(pkt[12:16], srcIP)
	copy(pkt[16:20], dstIP)
	binary.BigEndian.PutUint16(pkt[10:12], internetChecksum(pkt[:ipHL]))

	// --- ICMP header ---
	pkt[ipHL] = 3   // Type = Destination Unreachable
	pkt[ipHL+1] = 3 // Code = Port Unreachable

	// --- ICMP payload: cabecera IP original + 8 bytes UDP ---
	orig := ipHL + icmpHL
	pkt[orig] = 0x45
	binary.BigEndian.PutUint16(pkt[orig+2:orig+4], uint16(origIPHL+origUDPHL))
	pkt[orig+8] = 64
	pkt[orig+9] = 17 // UDP
	copy(pkt[orig+12:orig+16], dstIP) // original src = sender
	copy(pkt[orig+16:orig+20], srcIP) // original dst = target
	binary.BigEndian.PutUint16(pkt[orig+10:orig+12], internetChecksum(pkt[orig:orig+origIPHL]))

	udpOff := orig + origIPHL
	binary.BigEndian.PutUint16(pkt[udpOff:udpOff+2], id.RemotePort)
	binary.BigEndian.PutUint16(pkt[udpOff+2:udpOff+4], id.LocalPort)
	binary.BigEndian.PutUint16(pkt[udpOff+4:udpOff+6], origUDPHL)

	// Checksum ICMP (cubre header + payload)
	binary.BigEndian.PutUint16(pkt[ipHL+2:ipHL+4], internetChecksum(pkt[ipHL:]))

	log.Printf("[udp] port unreachable %s:%d -> ICMP a %s",
		id.LocalAddress, id.LocalPort, id.RemoteAddress)
	ns.sendToTunnel(pkt)
}

// internetChecksum implementa el checksum de Internet (RFC 1071), usado tanto
// para cabeceras IPv4 como para ICMP.
func internetChecksum(data []byte) uint16 {
	var sum uint32
	n := len(data)
	for i := 0; i+1 < n; i += 2 {
		sum += uint32(data[i])<<8 | uint32(data[i+1])
	}
	if n%2 == 1 {
		sum += uint32(data[n-1]) << 8
	}
	for sum > 0xffff {
		sum = (sum >> 16) + (sum & 0xffff)
	}
	return ^uint16(sum)
}
