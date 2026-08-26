"""Plano de streams de Pivx (Fase 3): port-forwarding L4 y SOCKS5.

Multiplexa multiples streams TCP sobre la MISMA conexion WebSocket del agente,
en paralelo al tunel L3 raw-IP (ver runtime.py / netstack.go). La discriminacion
de frames binarios es por el primer byte:

  - nibble 0x4 / 0x6  -> paquete IP  (tunel L3, lo maneja runtime.on_agent_packet)
  - 0x01              -> frame MUX    (payload de un stream, lo maneja este modulo)

Formato del frame binario MUX:  [0x01][stream_id uint32 BE][payload]

Ciclo de vida del stream por el plano de control (texto/JSON):
  stream_open / stream_open_ack / stream_close, y para remote-forward
  rforward_start / rforward_stop.

Modelo de hilos: TODO corre en el event loop del servidor WebSocket. La UI de
Streamlit (otro hilo) invoca la API publica *_sync, que agenda corrutinas en ese
loop con run_coroutine_threadsafe. Asi no hay estado compartido entre hilos.

Ordenacion / datos tempranos: los frames MUX de un stream pueden llegar antes de
que su socket local este listo (p.ej. en remote-forward el agente bombea nada
mas aceptar, mientras el servidor aun marca hacia su destino local). Por eso el
stream se registra de forma SINCRONA y `on_agent_binary` bufferiza en `pending`
hasta que el stream esta `ready`; al quedar listo se vuelca el buffer en orden.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import socket
from dataclasses import dataclass, field

from . import runtime

log = logging.getLogger("pivx.forward")

# --- framing MUX ----------------------------------------------------------

MUX_DATA = 0x01
_COPY_CHUNK = 32 * 1024
_ACK_TIMEOUT = 15.0


def is_mux_frame(b: bytes) -> bool:
    return len(b) > 0 and b[0] == MUX_DATA


def encode_mux(stream_id: int, payload: bytes) -> bytes:
    return bytes([MUX_DATA]) + stream_id.to_bytes(4, "big") + payload


def decode_mux(frame: bytes) -> tuple[int, bytes]:
    return int.from_bytes(frame[1:5], "big"), frame[5:]


# --- estado ---------------------------------------------------------------


@dataclass
class _Stream:
    sid: int
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    ack: asyncio.Future | None = None      # streams server->agent esperan ACK
    ready: bool = False                    # ya se puede escribir en el socket
    pending: list[bytes] = field(default_factory=list)  # datos tempranos


@dataclass
class _LocalForward:
    fwd_id: str
    bind: str          # "127.0.0.1:8080" (lado servidor)
    remote_dst: str    # "10.10.20.5:80"  (lo marca el agente)
    server: asyncio.AbstractServer | None = None


@dataclass
class _RemoteForward:
    fwd_id: str
    bind: str          # "0.0.0.0:4444"   (lado victima, lo abre el agente)
    local_dst: str     # "127.0.0.1:5555" (lo marca el servidor al recibir conexion)


@dataclass
class _State:
    loop: asyncio.AbstractEventLoop | None = None
    agent_id: str | None = None
    streams: dict[int, _Stream] = field(default_factory=dict)
    local_fwds: dict[str, _LocalForward] = field(default_factory=dict)
    remote_fwds: dict[str, _RemoteForward] = field(default_factory=dict)
    sid_counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    fwd_counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    socks_server: asyncio.AbstractServer | None = None
    socks_bind: str | None = None


_state = _State()


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    _state.loop = loop


def set_target_agent(agent_id: str | None) -> None:
    """Fija el agente destino de los forwards (modelo CTF de un solo agente)."""
    _state.agent_id = agent_id


def target_agent() -> str | None:
    return _state.agent_id


def _alloc_sid() -> int:
    # Ids del servidor: bit alto a 0 (los del agente lo tienen a 1) -> sin colision.
    return next(_state.sid_counter) & 0x7FFFFFFF


# --- envio hacia el agente -------------------------------------------------


async def _send_control(msg: dict) -> bool:
    ws = runtime.get_websocket(_state.agent_id) if _state.agent_id else None
    if ws is None:
        return False
    try:
        await ws.send(json.dumps(msg))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Error enviando control al agente: %s", e)
        return False


async def _send_data(sid: int, payload: bytes) -> bool:
    ws = runtime.get_websocket(_state.agent_id) if _state.agent_id else None
    if ws is None:
        return False
    try:
        await ws.send(encode_mux(sid, payload))
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Error enviando datos MUX al agente: %s", e)
        return False


# --- ciclo de vida de un stream -------------------------------------------


async def _mark_ready_and_pump(st: _Stream) -> None:
    """Marca el stream listo, vuelca los datos tempranos en orden y arranca el
    bombeo local->agente. Seccion critica sin awaits hasta encolar `pending`.
    """
    pend = st.pending
    st.pending = []
    st.ready = True
    if pend and st.writer is not None:
        st.writer.write(b"".join(pend))
    try:
        if st.writer is not None:
            await st.writer.drain()
    except Exception:  # noqa: BLE001
        await _close_stream(st.sid, notify=True)
        return
    _state.loop.create_task(_pump_local_to_agent(st))


async def _open_via_agent(dst: str, reader, writer) -> tuple[bool, str, _Stream | None]:
    """Pide al agente marcar hacia `dst`. Devuelve (ok, err, stream) SIN arrancar
    el bombeo: el llamador decide cuando (p.ej. SOCKS envia su respuesta antes).
    """
    if _state.agent_id is None:
        return False, "sin agente", None
    sid = _alloc_sid()
    ack: asyncio.Future = _state.loop.create_future()
    st = _Stream(sid=sid, reader=reader, writer=writer, ack=ack)
    _state.streams[sid] = st  # registro SINCRONO: los datos tempranos se bufferean

    if not await _send_control(
        {"type": "stream_open", "agent_id": _state.agent_id,
         "payload": {"stream_id": sid, "dst": dst}}
    ):
        _cleanup_stream(sid)
        return False, "no se pudo enviar al agente", None

    try:
        ok, err = await asyncio.wait_for(ack, timeout=_ACK_TIMEOUT)
    except asyncio.TimeoutError:
        ok, err = False, "timeout esperando ACK del agente"
    if not ok:
        _cleanup_stream(sid)
        return False, err, None
    return True, "", st


async def _pump_local_to_agent(st: _Stream) -> None:
    """Lee del socket local y reenvia como frames MUX hacia el agente."""
    try:
        while True:
            data = await st.reader.read(_COPY_CHUNK)
            if not data:
                break
            if not await _send_data(st.sid, data):
                break
    except Exception:  # noqa: BLE001
        pass
    finally:
        await _close_stream(st.sid, notify=True)


async def _close_stream(sid: int, notify: bool) -> None:
    st = _state.streams.pop(sid, None)
    if st is None:
        return
    if st.writer is not None:
        try:
            st.writer.close()
        except Exception:  # noqa: BLE001
            pass
    if notify:
        await _send_control(
            {"type": "stream_close", "agent_id": _state.agent_id,
             "payload": {"stream_id": sid}}
        )


def _cleanup_stream(sid: int) -> None:
    st = _state.streams.pop(sid, None)
    if st is not None and st.writer is not None:
        try:
            st.writer.close()
        except Exception:  # noqa: BLE001
            pass


# --- recepcion desde el agente --------------------------------------------


async def on_agent_binary(agent_id: str, frame: bytes) -> None:
    """Frame binario MUX del agente -> escribir el payload en el socket local."""
    sid, payload = decode_mux(frame)
    st = _state.streams.get(sid)
    if st is None:
        return
    if not st.ready or st.writer is None:
        st.pending.append(payload)  # datos tempranos: se vuelcan al quedar ready
        return
    try:
        st.writer.write(payload)
        await st.writer.drain()
    except Exception:  # noqa: BLE001
        await _close_stream(sid, notify=True)


async def on_agent_control(agent_id: str, msg: dict) -> None:
    """Control del plano de streams recibido del agente."""
    mtype = msg.get("type")
    p = msg.get("payload", {}) or {}
    sid = p.get("stream_id")

    if mtype == "stream_open_ack":
        st = _state.streams.get(sid)
        if st is not None and st.ack is not None and not st.ack.done():
            st.ack.set_result((bool(p.get("ok")), p.get("err", "")))

    elif mtype == "stream_close":
        await _close_stream(sid, notify=False)

    elif mtype == "stream_open":
        # remote-forward: el agente acepto una conexion en la victima. Registramos
        # el stream de forma SINCRONA (para bufferizar datos tempranos) y marcamos
        # hacia el destino local en una tarea aparte.
        fwd_id = p.get("fwd_id", "")
        if sid is None:
            return
        st = _Stream(sid=sid)
        _state.streams[sid] = st
        _state.loop.create_task(_accept_remote_stream(sid, fwd_id))


async def _accept_remote_stream(sid: int, fwd_id: str) -> None:
    """remote-forward: marca hacia el destino local y conecta el stream ya
    registrado (que puede tener datos tempranos en `pending`)."""
    st = _state.streams.get(sid)
    if st is None:
        return
    cfg = _state.remote_fwds.get(fwd_id)
    if cfg is None:
        await _reject_remote(sid, "fwd desconocido")
        return
    host, _, port = cfg.local_dst.rpartition(":")
    try:
        reader, writer = await asyncio.open_connection(host, int(port))
    except Exception as e:  # noqa: BLE001
        log.info("remote-forward %s: destino local %s inalcanzable: %s",
                 fwd_id, cfg.local_dst, e)
        await _reject_remote(sid, str(e))
        return
    st.reader = reader
    st.writer = writer
    await _send_control(
        {"type": "stream_open_ack", "agent_id": _state.agent_id,
         "payload": {"stream_id": sid, "ok": True}}
    )
    log.info("remote-forward %s: stream %d -> %s", fwd_id, sid, cfg.local_dst)
    await _mark_ready_and_pump(st)


async def _reject_remote(sid: int, err: str) -> None:
    _state.streams.pop(sid, None)
    await _send_control(
        {"type": "stream_open_ack", "agent_id": _state.agent_id,
         "payload": {"stream_id": sid, "ok": False, "err": err}}
    )


# --- LOCAL forward ---------------------------------------------------------


async def _start_local_forward(fwd_id: str, host: str, port: int, remote_dst: str) -> None:
    async def _on_client(reader, writer):
        ok, err, st = await _open_via_agent(remote_dst, reader, writer)
        if not ok:
            log.info("local-forward %s: %s", fwd_id, err)
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
            return
        await _mark_ready_and_pump(st)

    server = await asyncio.start_server(_on_client, host, port)
    _state.local_fwds[fwd_id] = _LocalForward(
        fwd_id=fwd_id, bind=f"{host}:{port}", remote_dst=remote_dst, server=server
    )
    log.info("local-forward %s: %s:%d -> (agente) %s", fwd_id, host, port, remote_dst)


async def _stop_local_forward(fwd_id: str) -> None:
    fwd = _state.local_fwds.pop(fwd_id, None)
    if fwd and fwd.server is not None:
        fwd.server.close()
        try:
            await fwd.server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        log.info("local-forward %s detenido", fwd_id)


# --- REMOTE forward --------------------------------------------------------


async def _start_remote_forward(fwd_id: str, bind: str, local_dst: str) -> None:
    _state.remote_fwds[fwd_id] = _RemoteForward(fwd_id=fwd_id, bind=bind, local_dst=local_dst)
    await _send_control(
        {"type": "rforward_start", "agent_id": _state.agent_id,
         "payload": {"fwd_id": fwd_id, "bind": bind}}
    )
    log.info("remote-forward %s: (victima) %s -> %s", fwd_id, bind, local_dst)


async def _stop_remote_forward(fwd_id: str) -> None:
    if _state.remote_fwds.pop(fwd_id, None) is not None:
        await _send_control(
            {"type": "rforward_stop", "agent_id": _state.agent_id,
             "payload": {"fwd_id": fwd_id}}
        )
        log.info("remote-forward %s detenido", fwd_id)


# --- SOCKS5 (proxy dinamico L7) -------------------------------------------
#
# Proxy SOCKS5 sin autenticacion. Acepta CONNECT, extrae el destino (IPv4,
# dominio o IPv6) y delega la resolucion + conexion TCP al AGENTE via _open_via_agent
# (por eso el DNS se resuelve del lado victima). Pensado para dirsearch, wfuzz,
# ffuf, Burp, etc. apuntando a socks5://127.0.0.1:1080.

_SOCKS_NO_AUTH = b"\x05\x00"
_SOCKS_OK = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"       # succeeded, BND 0.0.0.0:0
_SOCKS_FAIL = b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00"     # connection refused
_SOCKS_CMD_NOSUP = b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00"  # command not supported
_SOCKS_ATYP_NOSUP = b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00"  # addr type not supported


async def _handle_socks_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        # 1) Saludo: VER, NMETHODS, METHODS...
        greeting = await reader.readexactly(2)
        if greeting[0] != 0x05:
            writer.close()
            return
        await reader.readexactly(greeting[1])  # metodos ofrecidos (ignorados)
        writer.write(_SOCKS_NO_AUTH)
        await writer.drain()

        # 2) Peticion: VER, CMD, RSV, ATYP, DST.ADDR, DST.PORT
        req = await reader.readexactly(4)
        ver, cmd, _rsv, atyp = req[0], req[1], req[2], req[3]
        if ver != 0x05:
            writer.close()
            return
        if cmd != 0x01:  # solo CONNECT
            writer.write(_SOCKS_CMD_NOSUP)
            await writer.drain()
            writer.close()
            return

        if atyp == 0x01:  # IPv4
            host = socket.inet_ntoa(await reader.readexactly(4))
            dst_host = host
        elif atyp == 0x03:  # dominio (lo resuelve el agente en la red interna)
            ln = (await reader.readexactly(1))[0]
            host = (await reader.readexactly(ln)).decode("ascii", "ignore")
            dst_host = host
        elif atyp == 0x04:  # IPv6
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
            dst_host = f"[{host}]"
        else:
            writer.write(_SOCKS_ATYP_NOSUP)
            await writer.drain()
            writer.close()
            return

        port = int.from_bytes(await reader.readexactly(2), "big")
        dst = f"{dst_host}:{port}"

        # 3) Delegar la conexion al agente. Los bytes que el destino envie primero
        #    quedan bufferizados en el stream hasta que respondamos y arranque el bombeo.
        ok, err, st = await _open_via_agent(dst, reader, writer)
        writer.write(_SOCKS_OK if ok else _SOCKS_FAIL)
        await writer.drain()
        if not ok:
            log.info("socks: CONNECT %s rechazado: %s", dst, err)
            writer.close()
            return
        await _mark_ready_and_pump(st)
    except (asyncio.IncompleteReadError, ConnectionError, OSError):
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def _start_socks(host: str, port: int) -> None:
    if _state.socks_server is not None:
        return
    server = await asyncio.start_server(_handle_socks_client, host, port)
    _state.socks_server = server
    _state.socks_bind = f"{host}:{port}"
    log.info("SOCKS5 escuchando en %s:%d (destino: agente %s)", host, port, _state.agent_id)


async def _stop_socks() -> None:
    if _state.socks_server is not None:
        _state.socks_server.close()
        try:
            await _state.socks_server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        _state.socks_server = None
        _state.socks_bind = None
        log.info("SOCKS5 detenido")


# --- limpieza al caerse el agente -----------------------------------------


async def _drop_agent(agent_id: str) -> None:
    if _state.agent_id != agent_id:
        return
    for sid in list(_state.streams):
        await _close_stream(sid, notify=False)
    for fwd_id in list(_state.local_fwds):
        await _stop_local_forward(fwd_id)
    _state.remote_fwds.clear()
    log.info("Streams/forwards del agente %s liberados", agent_id)


# --- API sincrona para Streamlit (otro hilo) ------------------------------


def _run(coro, timeout: float = 10.0):
    loop = _state.loop
    if loop is None:
        raise RuntimeError("El listener aun no ha arrancado.")
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)


def _new_fwd_id(prefix: str) -> str:
    return f"{prefix}-{next(_state.fwd_counter):03d}"


def start_local_forward_sync(host: str, port: int, remote_dst: str) -> str:
    if _state.agent_id is None:
        raise RuntimeError("No hay agente seleccionado para el forward.")
    fwd_id = _new_fwd_id("L")
    _run(_start_local_forward(fwd_id, host, port, remote_dst))
    return fwd_id


def stop_local_forward_sync(fwd_id: str) -> None:
    _run(_stop_local_forward(fwd_id))


def start_remote_forward_sync(bind: str, local_dst: str) -> str:
    if _state.agent_id is None:
        raise RuntimeError("No hay agente seleccionado para el forward.")
    fwd_id = _new_fwd_id("R")
    _run(_start_remote_forward(fwd_id, bind, local_dst))
    return fwd_id


def stop_remote_forward_sync(fwd_id: str) -> None:
    _run(_stop_remote_forward(fwd_id))


DEFAULT_SOCKS_HOST = "127.0.0.1"
DEFAULT_SOCKS_PORT = 1080


def start_socks_sync(host: str = DEFAULT_SOCKS_HOST, port: int = DEFAULT_SOCKS_PORT) -> None:
    _run(_start_socks(host, port))


def stop_socks_sync() -> None:
    _run(_stop_socks())


def drop_agent(agent_id: str) -> None:
    """Llamado desde el hilo del loop (ws_server) al desconectarse el agente."""
    if _state.loop is not None:
        asyncio.run_coroutine_threadsafe(_drop_agent(agent_id), _state.loop)


def status() -> dict:
    return {
        "agent_id": _state.agent_id,
        "active_streams": len(_state.streams),
        "local_forwards": [
            {"id": f.fwd_id, "bind": f.bind, "dst": f.remote_dst}
            for f in _state.local_fwds.values()
        ],
        "remote_forwards": [
            {"id": f.fwd_id, "bind": f.bind, "dst": f.local_dst}
            for f in _state.remote_fwds.values()
        ],
        "socks_running": _state.socks_server is not None,
        "socks_bind": _state.socks_bind,
    }
