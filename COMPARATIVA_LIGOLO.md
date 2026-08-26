# Comparativa Técnica: Pivx vs Ligolo-ng

> Análisis profundo del código fuente de [Ligolo-ng](https://github.com/nicocha30/ligolo-ng) para identificar ventajas competitivas de Pivx y buenas prácticas a importar.
>
> Fecha de análisis: 2026-08-25
> Versión de Ligolo-ng analizada: commit más reciente en `main`

---

## En qué estamos mejor nosotros (Pivx Wins)

### 1. Netstack en el agente, no en el servidor

| | Pivx | Ligolo-ng |
|---|---|---|
| **Dónde corre gVisor** | En el **agente** (máquina comprometida) | En el **proxy/servidor** (máquina atacante) |
| **TUN requerido** | Solo en el servidor (Linux, raw-IP) | En el servidor (Windows/macOS/Linux/BSD) |
| **Root en el servidor** | Sí (para TUN) | Sí (para TUN + netstack) |
| **Root en el agente** | **No** | **No** |

**Por qué importa:** Al ejecutar netstack en el agente, Pivx procesa los paquetes L3 _in situ_. El tráfico llega al servidor ya desmultiplexado como streams L4, lo que reduce la complejidad del servidor. En Ligolo-ng, el servidor debe levantar un TUN multiplataforma (`wireguard/tun` para Windows/macOS/BSD) y un netstack completo — más superficie de ataque y más dependencias.

**Archivo clave en Ligolo-ng:** `pkg/proxy/netstack/stack.go` — todo el stack gVisor corre en el proxy.

### 2. gVisor oficial vs fork privado

| | Pivx | Ligolo-ng |
|---|---|---|
| **Dependencia** | `gvisor.dev/gvisor` (oficial) | `github.com/nicocha30/gvisor-ligolo` (fork) |
| **Actualizaciones** | Upstream directo | Dependiente del mantenedor del fork |
| **Riesgo** | Posibles roturas de API (ya resueltas) | API congelada pero sin parches de seguridad |

**Por qué importa:** El fork de gVisor evita roturas de API pero acumula deuda técnica. Ya demostramos que podemos mantener compatibilidad con la API upstream (parche `udp.ForwarderHandler` → `bool` return). Estar en upstream significa recibir fixes de seguridad y mejoras de rendimiento sin esperar a un tercero.

### 3. MUX custom ultraligero vs yamux

| | Pivx | Ligolo-ng |
|---|---|---|
| **Multiplexor** | Custom: `[0x01][stream_id u32 BE][payload]` | `hashicorp/yamux` |
| **Overhead por frame** | **5 bytes** | **12 bytes** (type + flags + stream_id + length) |
| **Colisión de IDs** | Bit 31: servidor=0, agente=1 | yamux maneja con client/server roles |
| **Window updates** | No necesarios (backpressure vía Queue) | Sí (frames adicionales de control) |

**Por qué importa:** En redes lentas o con alta latencia (pivoting a través de múltiples saltos), cada byte cuenta. Nuestro MUX tiene 58% menos overhead por frame. yamux envía window updates periódicos, keepalive frames, y flags adicionales que consumen ancho de banda sin aportar valor en un escenario de túnel.

**Archivo clave en Ligolo-ng:** `pkg/proxy/netstack/handlers.go:217` — cada conexión TCP/UDP abre un nuevo `yamuxConn.Open()`.

### 4. WebSocket con framing híbrido inteligente

| | Pivx | Ligolo-ng |
|---|---|---|
| **Transporte** | WebSocket con discriminación text/binary | TLS directo **o** WebSocket + yamux |
| **Control plane** | Text frames (JSON) | Mensajes gob sobre yamux streams |
| **Data plane** | Binary frames con nibble discrimination | Todo pasa por yamux |

**Primer byte del frame binario en Pivx:**
- `0x4_` → Paquete IPv4
- `0x6_` → Paquete IPv6
- `0x01` → Frame MUX

**Por qué importa:** El framing híbrido de Pivx permite que el plano de control (register, ping, route suggestions) y el plano de datos (paquetes L3, streams MUX) coexistan sobre una sola conexión WebSocket sin interferirse. Ligolo-ng necesita abrir un stream yamux por cada operación de control, añadiendo latencia al handshake.

### 5. Dashboard visual vs CLI interactiva

| | Pivx | Ligolo-ng |
|---|---|---|
| **Interfaz** | Streamlit dashboard (web) | CLI interactiva (terminal) |
| **Métricas** | Tiempo real, visual | Logs de texto |
| **Route management** | 1-click subnet discovery | Comandos manuales |
| **Port forwarding** | Tabs visuales (local/remote) | Comandos con sintaxis específica |
| **SOCKS5** | Integrado con toggle visual | No incluido |

**Por qué importa:** En un CTF bajo presión de tiempo, un dashboard visual reduce errores y acelera la toma de decisiones. El 1-click subnet discovery evita tener que calcular subnets manualmente.

### 6. Anti-suicidio automático

Pivx detecta automáticamente la subnet del uplink del agente y la excluye de las sugerencias de ruta. Esto previene el escenario donde un operador añade una ruta que captura el tráfico del propio túnel, cortando la conectividad.

Ligolo-ng no tiene esta protección — el usuario debe ser cuidadoso al añadir rutas con `ip route add`.

**Archivo clave en Pivx:** `agent/main.go` — detección de uplink y exclusión automática.

### 7. SOCKS5 integrado con resolución DNS remota

| | Pivx | Ligolo-ng |
|---|---|---|
| **SOCKS5** | Integrado, resolución DNS en el agente | No incluido |
| **Alternativa** | — | proxychains + TUN routing |

**Por qué importa:** SOCKS5 con resolución DNS remota permite que herramientas como `curl`, `nmap -sT`, y navegadores accedan a servicios internos sin configurar TUN ni rutas. Es plug-and-play.

### 8. Persistencia de estado (DuckDB)

Pivx persiste agentes, logs de conexión y rutas en DuckDB. Si el servidor se reinicia, el historial se mantiene. Ligolo-ng no persiste ningún estado — cada reinicio del proxy es borrón y cuenta nueva.

---

## Buenas Prácticas a Importar (Ligolo-ng Best Practices)

### 1. ICMP Echo Support (smartping) — PRIORIDAD ALTA

**Qué hace Ligolo-ng:** Cuando el netstack recibe un ICMP Echo Request, en lugar de responder automáticamente, envía un `HostPingRequestPacket` al agente. El agente ejecuta `smartping.TryResolve()`:

1. Intenta ICMP raw socket (`go-ping/ping`)
2. Si falla (sin privilegios), ejecuta el comando `ping` del sistema
3. Si el host está vivo, el proxy genera un ICMP Echo Reply real

**Archivos:** `pkg/proxy/netstack/icmp.go`, `pkg/proxy/netstack/handlers.go:44-90`, `pkg/agent/smartping/pinger.go`

**Por qué importarlo:** `ping` y `nmap -sn` son las primeras herramientas que un pentester usa al llegar a una nueva red. Sin soporte ICMP, el operador tiene que usar alternativas como `nmap -sT -Pn`, lo que añade fricción y ruido.

**Cómo adaptarlo a Pivx:** Como nuestro netstack está en el agente, la implementación es más simple: el agente puede hacer el ping directamente sin round-trip al servidor. Interceptar ICMP Echo en el netstack del agente → ejecutar ping local → responder ICMP Echo Reply directo al TUN del servidor.

### 2. Deshabilitar SYN-Cookies para compatibilidad con nmap — PRIORIDAD ALTA

```go
// Ligolo-ng: pkg/proxy/netstack/stack.go:238
synCookies := tcpip.TCPAlwaysUseSynCookies(false)
ns.SetTransportProtocolOption(tcp.ProtocolNumber, &synCookies)
```

**Por qué importarlo:** Con SYN-Cookies habilitados, gVisor puede responder a SYN con cookies sin crear estado real. Esto confunde a nmap que interpreta todos los puertos como abiertos. Deshabilitar SYN-Cookies mejora la fidelidad de los escaneos SYN (`nmap -sS`).

**Impacto:** Una línea de código en nuestro `agent/netstack.go`.

### 3. Magic IP (240.0.0.0/4) → localhost del agente — PRIORIDAD MEDIA

**Qué hace Ligolo-ng:** Cualquier IP en el rango 240.0.0.0/4 (Class E, reservado) se redirige automáticamente a `127.0.0.1` del agente.

```go
// Ligolo-ng: pkg/proxy/netstack/handlers.go:206-215
magicNet := net.IPNet{
    IP:   net.IPv4(240, 0, 0, 0),
    Mask: []byte{0xf0, 0x00, 0x00, 0x00},
}
if magicNet.Contains(net.ParseIP(targetIp)) {
    targetIp = "127.0.0.1"
}
```

**Por qué importarlo:** Permite acceder a servicios que escuchan solo en localhost del agente (bases de datos, paneles admin, APIs internas) sin configuración adicional. Solo necesitas `curl 240.0.0.1:8080` para llegar al `127.0.0.1:8080` del agente.

### 4. UDP Port Unreachable (ICMP) — PRIORIDAD MEDIA

**Qué hace Ligolo-ng:** Cuando una conexión UDP falla en el agente, genera un paquete ICMP "Port Unreachable" que se envía de vuelta al emisor a través del TUN.

**Archivo:** `pkg/proxy/netstack/handlers.go:94-174` — función `sendUDPPortUnreachable()`

**Por qué importarlo:** Sin esto, un escaneo UDP (`nmap -sU`) no puede distinguir entre puertos filtrados y cerrados, ya que nunca recibe la respuesta ICMP esperada. Con esta feature, nmap obtiene resultados precisos.

### 5. RST inteligente basado en errno del syscall — PRIORIDAD MEDIA

```go
// Ligolo-ng: pkg/agent/handler.go:136-142
var serr syscall.Errno
if errors.As(err, &serr) {
    if neterror.HostResponded(serr) {
        connectPacket.Reset = true
    }
}
```

**Por qué importarlo:** Cuando una conexión TCP falla, Ligolo-ng detecta si el sistema remoto respondió (connection refused = RST) vs no respondió (timeout = silencio). Esto permite que el proxy envíe un RST real de vuelta, mejorando la fidelidad del escaneo de puertos.

### 6. Framed UDP Relay — PRIORIDAD BAJA

**Qué hace Ligolo-ng:** `StartFramedPacketRelay()` en `pkg/relay/relay.go` envuelve cada datagrama UDP en un frame con header de 6 bytes `[type(1)][error(1)][length(4)]`. Esto preserva los límites de los datagramas que `io.Copy` pierde.

**Por qué importarlo (parcialmente):** Nuestro MUX ya delimita streams, pero para UDP podríamos mejorar la preservación de límites de datagramas dentro de un stream MUX.

### 7. Agent Kill remoto — PRIORIDAD BAJA

```go
// Ligolo-ng: pkg/agent/handler.go:377-379
case *protocol.AgentKillRequestPacket:
    os.Exit(0)
```

**Por qué importarlo:** Útil para limpieza post-CTF. Un botón "Kill Agent" en el dashboard de Pivx permitiría limpiar agentes desplegados sin necesidad de acceso al host.

### 8. TLS con Let's Encrypt / autocert — PRIORIDAD CONTEXTUAL

**Qué hace Ligolo-ng:** Soporte completo de TLS con tres opciones:
- Let's Encrypt automático (requiere port 80)
- Self-signed con cache en disco (ECDSA P-256)
- Certificados personalizados (certfile/keyfile)

**Archivos:** `pkg/tlsutils/certmanager.go`, `pkg/tlsutils/selfcert.go`

**Por qué importarlo (condicional):** Si Pivx necesita operar en escenarios donde el tráfico WebSocket debe parecer HTTPS legítimo (evasión de inspección), TLS con certificados reales es esencial. Para CTFs locales, no es prioritario.

### 9. Soporte TUN multiplataforma — NO IMPORTAR

Ligolo-ng usa `wireguard/tun` para soportar TUN en Windows (wintun), macOS, FreeBSD y OpenBSD. Como nuestro netstack está en el agente y el TUN en el servidor (Linux), no necesitamos soporte multiplataforma para el TUN. Si en el futuro moviéramos el servidor a Windows/macOS, esta es la referencia.

### 10. Session Recovery (ResetMultiplexer) — EVALUAR

**Qué hace Ligolo-ng:** Cuando la conexión se pierde y se reconecta, los listeners existentes pueden reconectarse a la nueva sesión yamux sin recrearse.

**Archivo:** `pkg/proxy/listeners.go:75-88`

**Por qué evaluarlo:** En doble salto, si el primer hop se pierde y reconecta, recrear todos los listeners desde cero puede causar una ventana de interrupción. La capacidad de "reconectar" listeners existentes reduce el downtime.

---

## Resumen Ejecutivo

| Dimensión | Pivx | Ligolo-ng | Veredicto |
|---|---|---|---|
| Arquitectura netstack | Agente (sin root en servidor para netstack) | Servidor (requiere TUN multiplataforma) | **Pivx** |
| Dependencia gVisor | Upstream oficial | Fork privado | **Pivx** |
| Multiplexor | Custom 5B header | yamux 12B header | **Pivx** |
| Transporte | WebSocket híbrido | TLS/WS + yamux | **Pivx** |
| Interfaz | Dashboard Streamlit | CLI interactiva | **Pivx** |
| SOCKS5 | Integrado + DNS remoto | No incluido | **Pivx** |
| Persistencia | DuckDB | Ninguna | **Pivx** |
| Anti-suicidio | Automático | No existe | **Pivx** |
| ICMP/ping | No soportado | smartping + ICMP reply | **Ligolo-ng** |
| Fidelidad escaneo | Básica | RST inteligente + ICMP unreachable | **Ligolo-ng** |
| TLS/autocert | No incluido | Let's Encrypt + selfcert | **Ligolo-ng** |
| Multi-plataforma TUN | Linux only | Win/macOS/BSD/Linux | **Ligolo-ng** |

**Conclusión:** Pivx tiene ventajas arquitectónicas fundamentales (netstack en agente, MUX ligero, dashboard visual). Las mejoras más valiosas a importar de Ligolo-ng son las que mejoran la **fidelidad del tunneling** para herramientas de escaneo: ICMP echo, SYN-Cookies deshabilitados, RST inteligente, y UDP Port Unreachable.
