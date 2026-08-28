"""Servidor WebSocket del C2 de Pivx (Fase 2).

Multiplexa dos planos sobre una unica conexion por agente, usando el tipo de
frame WebSocket:
  - Frames de TEXTO  -> plano de control (JSON): register, ping.
  - Frames BINARIOS  -> plano de datos: paquetes IP crudos hacia/desde la TUN.

El plano de datos se delega a `runtime` (TUN + bombeo). Ver runtime.py y tun.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import websockets

from . import db, forward, runtime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [ws] %(message)s")
log = logging.getLogger("pivx.ws")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# Keep-alive: el agente pinea cada ~15s (ver agent/main.go). Damos margen a 3
# pings perdidos antes de declararlo muerto, y barremos con frecuencia mayor.
PING_TIMEOUT_SECONDS = 45.0
SWEEP_INTERVAL_SECONDS = 10.0

_started = False
_start_lock = threading.Lock()


async def _handle_agent(websocket) -> None:
    remote = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
    agent_id = "?"
    log.info("Nueva conexion desde %s", remote)
    try:
        async for raw in websocket:
            # --- Frame binario: puede ser tunel L3 o plano MUX de streams ---
            if isinstance(raw, (bytes, bytearray)):
                if agent_id == "?":
                    continue
                b = bytes(raw)
                # nibble 0x4/0x6 = paquete IP (tunel L3); 0x01 = frame MUX.
                if b and (b[0] >> 4 in (4, 6)):
                    runtime.on_agent_packet(agent_id, b)
                elif forward.is_mux_frame(b):
                    await forward.on_agent_binary(agent_id, b)
                continue

            # --- Plano de control: frame de texto = JSON ---
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Mensaje no-JSON de %s", remote)
                continue

            msg_type = msg.get("type")
            agent_id = msg.get("agent_id", agent_id)

            # Control del plano de streams (port-forwarding / SOCKS) -> forward.
            if msg_type in ("stream_open", "stream_open_ack", "stream_close"):
                await forward.on_agent_control(agent_id, msg)
                continue

            if msg_type == "register":
                p = msg.get("payload", {}) or {}
                db.register_agent(
                    agent_id=agent_id,
                    hostname=p.get("hostname", "unknown"),
                    os_name=p.get("os", "unknown"),
                    arch=p.get("arch", "unknown"),
                    version=p.get("version", "unknown"),
                    remote_addr=remote,
                    interfaces=p.get("interfaces", []),
                )
                db.log_event(agent_id, "registered", f"host={p.get('hostname')} {remote}")
                runtime.register_session(
                    runtime.AgentSession(agent_id=agent_id, websocket=websocket, remote=remote)
                )
                # Modelo CTF de un solo agente: fijarlo como destino de forwards.
                forward.set_target_agent(agent_id)
                log.info("Agente registrado: %s (%s)", agent_id, p.get("hostname"))
                await websocket.send(json.dumps({"type": "ack", "agent_id": agent_id}))

            elif msg_type == "ping":
                db.touch_agent(agent_id)
                db.log_event(agent_id, "ping")
                await websocket.send(json.dumps({"type": "pong", "agent_id": agent_id}))

            else:
                log.info("Mensaje de control desconocido de %s: %s", remote, msg_type)

    except websockets.ConnectionClosed:
        log.info("Conexion cerrada: %s (%s)", agent_id, remote)
    finally:
        if agent_id != "?":
            db.mark_offline(agent_id)
            db.log_event(agent_id, "disconnected", remote)
            runtime.unregister_session(agent_id)
            forward.drop_agent(agent_id)
            if forward.target_agent() == agent_id:
                forward.set_target_agent(None)


async def _keepalive_sweep() -> None:
    """Barre periodicamente agentes sin pings recientes y los marca offline."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        try:
            stale = await loop.run_in_executor(
                None, db.mark_stale_offline, PING_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001
            log.exception("Fallo en el barrido de keep-alive")
            continue
        for agent_id in stale:
            log.info("Agente %s marcado offline por timeout (sin pings)", agent_id)
            # Espejo en memoria: quitar la sesion para que las metricas de
            # Streamlit (que leen runtime._state.sessions) queden alineadas con
            # DuckDB al instante, sin esperar al cierre sucio del socket.
            runtime.expire_session(agent_id)
            await loop.run_in_executor(
                None, db.log_event, agent_id, "timeout",
                f"sin pings en {int(PING_TIMEOUT_SECONDS)}s",
            )


async def _serve(host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    runtime.set_loop(loop)
    forward.set_loop(loop)
    db.get_connection()
    db.mark_all_offline()
    log.info("Agentes fantasma marcados offline al arrancar")
    sweep = asyncio.create_task(_keepalive_sweep())
    # max_size=None: no limitar el tamano de frame (paquetes del plano de datos).
    try:
        async with websockets.serve(_handle_agent, host, port, max_size=None):
            log.info("Listener WebSocket escuchando en ws://%s:%d", host, port)
            await asyncio.Future()
    finally:
        sweep.cancel()


def _run_loop(host: str, port: int) -> None:
    asyncio.run(_serve(host, port))


def start_background_listener(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    global _started
    with _start_lock:
        if _started:
            return False
        thread = threading.Thread(
            target=_run_loop, args=(host, port), name="pivx-ws", daemon=True
        )
        thread.start()
        _started = True
        return True
