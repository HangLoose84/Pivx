"""Estado y orquestacion del plano de datos de Pivx (Fase 2).

Puentea tres mundos:
  - El bucle asyncio del servidor WebSocket (hilo secundario).
  - La interfaz TUN de Linux (E/S de paquetes bloqueante, integrada via add_reader).
  - El dashboard Streamlit (hilo principal), que activa/desactiva tuneles y rutas.

Modelo (MVP): un unico tunel activo a la vez. Al activar un agente se crea la TUN
`pivx0`; los paquetes leidos de la TUN se envian al WebSocket del agente activo, y
los paquetes binarios que llegan del agente se escriben de vuelta en la TUN.

Las operaciones que tocan la TUN o el add_reader DEBEN ejecutarse en el hilo del
bucle asyncio. Desde Streamlit se invocan con run_coroutine_threadsafe.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field

from . import db
from .tun import DEFAULT_MTU, TunDevice

log = logging.getLogger("pivx.runtime")

TUN_NAME = "pivx0"
TUN_MTU = DEFAULT_MTU  # debe coincidir con agent/netstack.go (tunMTU)

# Backpressure TUN -> WebSocket: si la TUN produce paquetes mas rapido de lo que
# el WebSocket los drena, la cola se llena y descartamos (drop) en vez de crecer
# sin limite (protege la RAM del servidor bajo trafico agresivo, p.ej. escaneos).
OUT_QUEUE_MAXSIZE = 1000


@dataclass
class AgentSession:
    """Conexion viva de un agente (referencia a su WebSocket)."""
    agent_id: str
    websocket: object  # websockets.WebSocketServerProtocol
    remote: str


@dataclass
class _State:
    loop: asyncio.AbstractEventLoop | None = None
    sessions: dict[str, AgentSession] = field(default_factory=dict)
    tun: TunDevice | None = None
    active_agent_id: str | None = None
    routes: set[str] = field(default_factory=set)
    out_queue: asyncio.Queue | None = None
    writer_task: asyncio.Task | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_state = _State()


# --- ciclo de vida del bucle / sesiones (llamado desde ws_server) ----------

def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    _state.loop = loop


def register_session(session: AgentSession) -> None:
    _state.sessions[session.agent_id] = session


def get_websocket(agent_id: str):
    """Devuelve el WebSocket vivo del agente, o None si no esta conectado.

    Lo usa el modulo `forward` (mux de streams) para enviar control/datos al
    agente sin acoplarse al estado interno de runtime.
    """
    sess = _state.sessions.get(agent_id)
    return sess.websocket if sess else None


def get_loop() -> asyncio.AbstractEventLoop | None:
    return _state.loop


def unregister_session(agent_id: str) -> None:
    _state.sessions.pop(agent_id, None)
    # Si el agente que se desconecta tenia el tunel activo, lo cerramos.
    if _state.active_agent_id == agent_id and _state.loop is not None:
        asyncio.run_coroutine_threadsafe(_deactivate(), _state.loop)


async def _safe_close(ws) -> None:
    """Cierra un WebSocket best-effort, ignorando que ya este muerto."""
    try:
        await ws.close()
    except Exception:  # noqa: BLE001
        pass


def expire_session(agent_id: str) -> None:
    """Expulsa una sesion por timeout de keep-alive (espejo en memoria de
    db.mark_stale_offline).

    A diferencia de unregister_session (que reacciona al cierre limpio del
    socket), esto se invoca cuando el agente lleva demasiado tiempo sin pings:
    la conexion puede seguir medio-viva en el servidor. Quita la sesion de
    _state.sessions para que las metricas de Streamlit dejen de contarla al
    instante, desactiva el tunel si era el agente activo, e intenta cerrar el
    socket colgado para liberar su handler.

    Pensado para llamarse desde el hilo del event loop (el barrido asyncio).
    """
    sess = _state.sessions.pop(agent_id, None)
    if sess is None:
        return  # ya se habia limpiado por cierre limpio del socket
    if _state.active_agent_id == agent_id and _state.loop is not None:
        asyncio.run_coroutine_threadsafe(_deactivate(), _state.loop)
    ws = getattr(sess, "websocket", None)
    if ws is not None and _state.loop is not None:
        asyncio.run_coroutine_threadsafe(_safe_close(ws), _state.loop)


def on_agent_packet(agent_id: str, data: bytes) -> None:
    """Paquete IP binario recibido del agente -> escribir en la TUN (si activo)."""
    if _state.active_agent_id == agent_id and _state.tun is not None:
        try:
            _state.tun.write(data)
        except OSError as e:
            log.warning("Error escribiendo en TUN: %s", e)


# --- estado consultable desde Streamlit ------------------------------------

def status() -> dict:
    return {
        "active_agent_id": _state.active_agent_id,
        "tun_name": _state.tun.name if _state.tun else None,
        "online_sessions": list(_state.sessions.keys()),
        "routes": sorted(_state.routes),
    }


# --- corrutinas del plano de datos (se ejecutan en el hilo del loop) -------

async def _activate(agent_id: str) -> None:
    if _state.active_agent_id is not None:
        await _deactivate()

    tun = TunDevice(TUN_NAME, mtu=TUN_MTU)
    os.set_blocking(tun.fd, False)  # el add_reader dispara en 'readable'

    _state.out_queue = asyncio.Queue(maxsize=OUT_QUEUE_MAXSIZE)
    _state.loop.add_reader(tun.fd, _on_tun_readable)
    _state.writer_task = _state.loop.create_task(_writer_loop())
    _state.tun = tun
    _state.active_agent_id = agent_id
    log.info("Tunel activado para %s (TUN %s)", agent_id, tun.name)


async def _deactivate() -> None:
    if _state.tun is not None:
        try:
            _state.loop.remove_reader(_state.tun.fd)
        except (ValueError, OSError):
            pass
        for cidr in list(_state.routes):
            _state.tun.del_route(cidr)
            db.remove_route(cidr)
        _state.routes.clear()
        _state.tun.close()
        _state.tun = None
    if _state.writer_task is not None:
        _state.writer_task.cancel()
        _state.writer_task = None
    _state.out_queue = None
    prev = _state.active_agent_id
    _state.active_agent_id = None
    if prev:
        log.info("Tunel desactivado para %s", prev)


def _on_tun_readable() -> None:
    """Callback del loop: hay paquetes que leer de la TUN."""
    tun = _state.tun
    q = _state.out_queue
    if tun is None or q is None:
        return
    try:
        data = tun.read()
    except (BlockingIOError, OSError):
        return
    if data:
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            log.warning("Cola TUN->WS llena, paquete descartado")


async def _writer_loop() -> None:
    """Drena la cola de paquetes de la TUN hacia el WebSocket del agente activo."""
    q = _state.out_queue
    try:
        while True:
            data = await q.get()
            sess = _state.sessions.get(_state.active_agent_id)
            if sess is None:
                continue
            try:
                await sess.websocket.send(data)  # frame binario
            except Exception as e:  # noqa: BLE001 - conexion caida
                log.warning("Error enviando al agente: %s", e)
                return
    except asyncio.CancelledError:
        pass


# --- API publica para Streamlit (hilo principal) ---------------------------

def _run_on_loop(coro, timeout: float = 10.0):
    if _state.loop is None:
        raise RuntimeError("El listener aun no ha arrancado.")
    fut = asyncio.run_coroutine_threadsafe(coro, _state.loop)
    return fut.result(timeout=timeout)


def activate_tunnel(agent_id: str) -> None:
    if agent_id not in _state.sessions:
        raise RuntimeError("Ese agente no esta conectado ahora mismo.")
    _run_on_loop(_activate(agent_id))


def deactivate_tunnel() -> None:
    _run_on_loop(_deactivate())


def add_route(cidr: str) -> None:
    with _state.lock:
        tun = _state.tun
        agent = _state.active_agent_id
    if tun is None:
        raise RuntimeError("No hay tunel activo: inicia uno antes de anadir rutas.")
    tun.add_route(cidr)            # subprocess 'ip route' (no requiere el loop)
    _state.routes.add(cidr)
    db.add_route(cidr, agent)


def del_route(cidr: str) -> None:
    tun = _state.tun
    if tun is not None:
        tun.del_route(cidr)
    _state.routes.discard(cidr)
    db.remove_route(cidr)


def kill_agent(agent_id: str) -> None:
    """Envia el comando kill al agente y limpia su sesion."""
    import json as _json

    sess = _state.sessions.get(agent_id)
    if sess is None:
        raise RuntimeError("Agente no conectado.")

    async def _send_kill() -> None:
        try:
            await sess.websocket.send(
                _json.dumps({"type": "kill", "agent_id": agent_id})
            )
        except Exception:  # noqa: BLE001
            pass

    _run_on_loop(_send_kill())
    db.log_event(agent_id, "killed", "kill remoto desde dashboard")
