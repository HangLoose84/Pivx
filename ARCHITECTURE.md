# Arquitectura de Pivx

> Herramienta de pivoting y enrutamiento de red. Objetivo: superar la usabilidad
> de Ligolo-ng mediante **automatización del enrutamiento** y una **UX** de
> primera clase (dashboard web desde el día 1).

Este documento describe la arquitectura. La **Fase 1** implementó el **plano de
control** (registro, estado, keep-alive); la **Fase 2** el **plano de datos L3**
(TUN + netstack gVisor + rutas); y la **Fase 3** una **suite de pivoting** con
port-forwarding L4 (local/remote) y proxy **SOCKS5** L7 multiplexados sobre el
mismo WebSocket, además de endurecer el data-plane (MTU, backpressure) y añadir
cross-build. Multi-agente simultáneo y TLS/auth quedan para fases posteriores.

---

## 1. Componentes

| Componente | Lenguaje | Rol | Ubicación |
|------------|----------|-----|-----------|
| **Agente** | Go (binario estático) | Se ejecuta en el host comprometido (*target*). Túnel de transporte. | Red interna víctima |
| **Servidor C2** | Python (Streamlit + asyncio) | Interfaz del operador, gestión de agentes y rutas. | Máquina del atacante |
| **Persistencia** | DuckDB (fichero local) | Estado de agentes y logs de conexión. | Junto al servidor |

```
   [ Operador ]          Máquina atacante                    Red interna víctima
        │                                                          
        │  navegador                                               
        ▼                                                          
  ┌───────────────┐   hilo secundario   ┌──────────────────┐  WebSocket   ┌───────────┐
  │  Streamlit UI │◄──── DuckDB ───────►│  Listener WS      │◄────────────►│  Agente   │
  │  (dashboard)  │   (estado/logs)     │  (asyncio thread) │  (TLS/wss)   │  (Go)     │
  └───────────────┘                     └──────────────────┘              └─────┬─────┘
                                                                                │
                                                                     acceso a la LAN interna
                                                                     (10.0.0.0/24, etc.)
```

---

## 2. Modelo de conexión: WebSockets

- El **agente inicia** la conexión saliente hacia el C2 (*reverse connection*).
  Esto atraviesa NAT/firewalls de egreso, que suelen permitir HTTP/HTTPS.
- El transporte es **WebSocket** (`ws://` en el MVP, **`wss://` con TLS** como
  objetivo), lo que permite mezclarse con tráfico web legítimo.
- Una **única conexión WebSocket por agente** transporta *todo*: el plano de
  control y (a futuro) el plano de datos, mediante **multiplexación**.

### Planos sobre una única conexión (diseño híbrido)

Una sola conexión WebSocket por agente transporta **tres** cosas, separadas por
el **tipo de frame** WS y, dentro de los binarios, por el **primer byte**:

| | Control (Fase 1 ✅) | Túnel L3 (Fase 2 ✅) | MUX de streams (Fase 3 ✅) |
|---|---|---|---|
| Contenido | registro, ping/pong, ciclo de vida de streams/rutas | paquetes IP crudos (L3) | bytes de streams TCP (L4/L7) |
| Formato | JSON (`ControlMessage`) | paquete IP crudo | `[0x01][stream_id u32 BE][payload]` |
| **Frame WS** | **TEXTO** | **BINARIO** | **BINARIO** |
| Discriminador | tipo de frame = texto | 1er byte, nibble `4`/`6` | 1er byte `0x01` (nibble `0x0`) |
| Frecuencia | baja | alta (túnel) | alta (forwards/SOCKS) |

> **La clave del diseño híbrido — discriminación por nibble.** Los frames de
> texto son siempre control. Para los binarios, ambos planos de datos conviven
> sin cabecera de sobra observando el **primer nibble**: un paquete IP real
> empieza *siempre* por la versión `0x4` (IPv4) o `0x6` (IPv6), mientras que un
> frame MUX empieza por `0x01` (nibble `0x0`), valor que **jamás** aparece como
> primer byte de un paquete IP. Así el túnel L3 queda **intacto byte a byte** (no
> se le añade ningún prefijo) y el canal MUX se cuela en el mismo flujo binario:
>
> ```
>   frame WS binario
>        │
>        ├─ 1er byte >>4 == 4 o 6  ──►  paquete IP  ──►  TUN / netstack (L3)
>        └─ 1er byte == 0x01       ──►  frame MUX   ──►  demux por stream_id (L4/L7)
> ```
>
> **Multiplexación en dos niveles.** Para el **túnel L3** no hace falta demux de
> flujos: lo resuelve el **netstack** del agente (una conexión = un
> `ForwarderRequest`). Para **port-forwarding y SOCKS** sí hay un mux propio y
> ligero (sin `yamux`/`smux`): cada stream TCP se identifica con un `stream_id`
> de 32 bits en la cabecera del frame MUX. Los ids del **servidor** llevan el bit
> alto a 0 y los del **agente** (remote-forward) a 1, de modo que comparten el
> mapa de streams sin colisión. El ciclo de vida (`stream_open`,
> `stream_open_ack`, `stream_close`, `rforward_start/stop`) viaja por el plano de
> control (texto).

---

## 3. Protocolo del plano de control (Fase 1)

Sobre (*envelope*) común, serializado como JSON:

```jsonc
{
  "type": "register | ping | pong | ack",
  "agent_id": "<uuid>",
  "payload": { /* específico del tipo */ }
}
```

Flujo del MVP:

1. **register** — El agente se conecta y envía `hostname`, `os`, `arch`,
   `version`. El servidor hace *upsert* en DuckDB y responde `ack`.
2. **ping / pong** — Keep-alive periódico; refresca `last_seen` y marca al
   agente `online`. Si la conexión se cierra, el agente pasa a `offline`.

El dashboard lee `agents` y `connection_logs` de DuckDB y refresca cada 3 s.

---

## 4. Plano de datos y enrutamiento (Fase 2 ✅)

Esta es la razón de ser de Pivx. **Ya implementado** en la Fase 2 (transporte y
netstack). El enrutamiento *automático* completo (sin intervención) se aplaza a
la Fase 5.

**Reparto de responsabilidades tal como está construido hoy:**

- **Servidor (Python, `pivx_server/tun.py` + `runtime.py`)** — crea la TUN
  `pivx0` en modo raw-IP (`IFF_TUN | IFF_NO_PI`), la integra en el bucle asyncio
  vía `loop.add_reader(fd)`, y bombea paquetes: `TUN → frame binario WS` y
  `frame binario WS → TUN`. **No** contiene pila TCP/IP.
- **Agente (Go, `netstack.go`)** — pila **gVisor** en userland. `InjectInbound`
  mete cada paquete en la pila; los *forwarders* TCP/UDP crean un socket real
  hacia `id.LocalAddress:id.LocalPort` (el destino interno) y hacen de proxy. Los
  paquetes de respuesta salen por el `channel.Endpoint` y vuelven al túnel.
- **Un único túnel activo a la vez** (MVP). Al desconectarse el agente activo, el
  servidor revierte automáticamente las rutas y cierra la TUN.

### 4.1 Interfaz TUN en el servidor

- El servidor C2 crea una **interfaz TUN** virtual (p. ej. `pivx0`) en la
  máquina del atacante. Una TUN opera en **capa 3**: entrega/recibe paquetes IP.
- El operador (o Pivx automáticamente) añade **rutas** en el SO del atacante que
  envían las subredes internas de la víctima a través de `pivx0`:

  ```
  ip route add 10.0.0.0/24 dev pivx0        # Linux
  # (equivalentes con la API de routing en Windows/macOS)
  ```

- Cualquier herramienta del atacante (nmap, curl, un navegador) que hable con
  `10.0.0.5` genera paquetes que el kernel envía a `pivx0`.

### 4.2 Ciclo de vida de un paquete

```
 herramienta atacante ──► kernel ──► pivx0 (TUN) ──► lee el servidor
        ▲                                                   │  encapsula el paquete IP
        │                                                   ▼
   entrega respuesta                                 WebSocket (frame binario)
        │                                                   │
        │                                                   ▼
 pivx0 (TUN) ◄── servidor ◄── WebSocket ◄── Agente crea un socket real hacia
                                              10.0.0.5 en la LAN interna y
                                              hace de proxy (userland networking)
```

- El **agente NO necesita privilegios de root/TUN**: reconstruye las conexiones
  en *userland* (abre sockets TCP/UDP reales hacia el destino interno), igual que
  el enfoque de Ligolo-ng. Esto simplifica el despliegue en el target.
- El **servidor sí** requiere privilegios para crear la TUN y añadir rutas.

### 4.3 Optimización del data-plane (Fase 3)

- **MTU 1350** en la TUN (`tun.py`) y en el netstack del agente (`netstack.go`):
  deja holgura para el overhead del framing WebSocket (y de un TLS/wss futuro)
  por debajo del 1500 de Ethernet, evitando fragmentación.
- **Backpressure con drop.** La cola `TUN → WebSocket` es una `asyncio.Queue`
  acotada (`maxsize=1000`). Si la TUN produce más rápido de lo que el WS drena
  (p. ej. un escaneo agresivo), se **descarta** el paquete en vez de bloquear el
  event loop o crecer sin límite en RAM. El agente aplica el mismo criterio en su
  canal de salida.
- **NO_PI obligatorio.** La TUN se abre con `IFF_TUN | IFF_NO_PI`: sin los 4
  bytes de metadatos que el kernel antepondría, el primer byte que ve el agente
  es ya el nibble de versión IP (imprescindible para la discriminación L3/MUX).

---

## 4bis. Plano de streams: port-forwarding L4 y SOCKS5 L7 (Fase 3)

Además del túnel L3, Pivx multiplexa **streams TCP** sobre la misma conexión
(canal MUX, ver arriba). Es lo que Ligolo-ng resuelve con *listeners*; aquí se
unifica en una sola abstracción de stream con tres consumidores:

- **Local forward (`-L`).** El **servidor** abre un listener (p. ej.
  `127.0.0.1:8080`); en cada `accept` asigna un `stream_id`, envía `stream_open`
  con el destino interno, y el **agente** marca (dial) hacia ese `IP:puerto` y
  hace de proxy. Expone un servicio interno en la máquina del operador.
- **Remote forward (`-R`).** El **agente** abre un listener en la red víctima
  (p. ej. `0.0.0.0:4444`, ideal para *reverse shells*); en cada `accept` abre un
  stream hacia el servidor, que lo conecta a su destino local (el *handler* del
  operador). Semántica tipo `ssh -R`.
- **SOCKS5 (L7).** El servidor levanta un proxy SOCKS5 (`127.0.0.1:1080`); tras
  el `CONNECT`, extrae el destino y lo delega al agente vía el mismo mecanismo de
  stream. La **resolución DNS y la conexión TCP ocurren en el agente**, por lo
  que los nombres internos se resuelven en la red víctima. Pensado para dirsearch,
  ffuf, Burp, proxychains, etc.

**Ciclo de vida de un stream** (control por frames de texto; datos por MUX):

```
  server ── stream_open{sid,dst} ──► agent           (L / SOCKS: el agente marca)
  agent  ── stream_open{sid,fwd_id} ─► server         (R: el servidor marca al handler local)
  ◄─────── stream_open_ack{sid,ok} ───────►
  ambos  ── [0x01][sid][bytes] ◄──────►  (datos, full-duplex)
  cualquiera ── stream_close{sid} ──►     (cierre)
```

**Ordenación / datos tempranos.** Un servicio que "habla primero" (banner FTP/SMTP,
una *reverse shell*) puede emitir bytes antes de que el otro extremo tenga listo
su socket local. Para no perderlos, el stream se **registra de forma síncrona** y
los frames MUX que lleguen antes de estar `ready` se **bufferan** y se vuelcan en
orden al establecerse. Sin esto, el primer paquete de un remote-forward se
perdería en la carrera entre `stream_open` (control) y el primer frame de datos.

**Independencia del L3.** Forwards y SOCKS operan solo sobre el canal MUX: **no**
requieren tener el túnel L3 (`pivx0`) activo; basta un agente conectado. En el
modelo CTF mono-agente, el agente se autoselecciona como destino al registrarse y
sus streams/forwards se liberan al desconectarse.

---

## 5. Automatización del enrutamiento (el diferenciador — Fase 5)

Donde Ligolo-ng exige pasos manuales, Pivx busca **automatizar**. Estado actual:

1. **Descubrimiento** ✅ — Al registrarse, el agente reporta sus interfaces y
   subredes conectadas (excluyendo la del uplink al C2, anti-suicidio). El
   servidor las muestra en el dashboard.
2. **Rutas sugeridas** ✅ — Pivx propone las rutas hacia esas subredes con un clic
   ("Enrutar 10.0.0.0/24 a través de este agente").
3. **Aplicación automática** ⬜ — Aplicar la ruta sin intervención al conectar el
   agente (hoy es 1-clic).
4. **Gestión de conflictos** ⬜ — Detección de solapamientos entre subredes de
   varios agentes y selección del *next-hop* adecuado (requiere multi-agente).
5. **Limpieza** ✅ — Al desconectarse un agente o cerrar la sesión, Pivx revierte
   automáticamente las rutas que había instalado (sin dejar el sistema sucio).

Esquema de la tabla `routes` (ya creada en DuckDB):

```sql
CREATE TABLE routes (
    cidr        VARCHAR,     -- p. ej. '10.0.0.0/24'
    agent_id    VARCHAR,     -- agente que actúa de next-hop
    status      VARCHAR,     -- active | removed
    created_at  TIMESTAMPTZ
);
```

---

## 6. Seguridad (roadmap)

- **wss:// + TLS** obligatorio fuera de laboratorio.
- **Autenticación del agente** mediante token/clave precompartida en el registro.
- Aislar el bind del listener (evitar `0.0.0.0` en producción salvo necesidad).
- Cifrado del túnel y validación de certificados.

> ⚠️ Pivx es una herramienta para **pruebas de penetración autorizadas** y
> ejercicios de *red team* con permiso explícito. Úsese solo en sistemas propios
> o con autorización por escrito.

---

## 7. Estado por fases

| Fase | Alcance | Estado |
|------|---------|--------|
| **1 — MVP base** | Estructura, WS control-plane, registro + ping, dashboard | ✅ |
| **2 — Túnel de datos** | TUN en servidor, netstack gVisor en agente, framing texto/binario, rutas 1-clic | ✅ |
| **2.5 — Estabilización** | Filtro anti-uplink, cierre TCP half-open, timeout de agentes (keep-alive) | ✅ |
| **3 — Suite de pivoting** | Data-plane endurecido (MTU/backpressure), canal **MUX**, port-forwarding **L4** (local/remote), **SOCKS5** L7, `Makefile` de cross-build | ✅ |
| 4 — Endurecimiento | TLS/wss, auth de agentes, cifrado | ⬜ |
| 5 — Multi-agente | Rutas automáticas al conectar, gestión de solapamientos, varios túneles simultáneos | ⬜ |

> **Nota Fase 3:** el pivoting es funcional en las tres capas (L3 rutas, L4
> forwards, L7 SOCKS) para un objetivo/agente. Lo que se **aplaza** es el
> multi-agente simultáneo y la automatización del enrutado (movidos a la Fase 5),
> y el endurecimiento con TLS/autenticación (Fase 4).
