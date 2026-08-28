"""Capa de persistencia de Pivx sobre DuckDB.

Guarda el estado de los agentes, las subredes descubiertas, un log de eventos y
las rutas instaladas.

NOTA de concurrencia: DuckDB solo permite un proceso con escritura a la vez.
Si el Auto-Pilot ya tiene el lock, Streamlit abre la BD en modo read_only
para funcionar como monitor sin colisionar.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "pivx.duckdb"

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None
_read_only: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn, _read_only
    with _lock:
        if _conn is None:
            try:
                _conn = duckdb.connect(str(DB_PATH))
                _read_only = False
            except duckdb.IOException:
                _conn = duckdb.connect(str(DB_PATH), read_only=True)
                _read_only = True
            if not _read_only:
                _init_schema(_conn)
        return _conn


def is_read_only() -> bool:
    return _read_only


def _init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id    VARCHAR PRIMARY KEY,
            hostname    VARCHAR,
            os          VARCHAR,
            arch        VARCHAR,
            version     VARCHAR,
            status      VARCHAR,
            first_seen  TIMESTAMPTZ,
            last_seen   TIMESTAMPTZ,
            remote_addr VARCHAR,
            interfaces  VARCHAR          -- JSON: [{name, cidrs:[...]}]
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS connection_logs (
            ts       TIMESTAMPTZ,
            agent_id VARCHAR,
            event    VARCHAR,
            detail   VARCHAR
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS routes (
            cidr       VARCHAR,
            agent_id   VARCHAR,
            status     VARCHAR,          -- active | removed
            created_at TIMESTAMPTZ
        );
        """
    )
    # Migracion suave por si la tabla venia de la Fase 1 sin la columna.
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS interfaces VARCHAR")
    except duckdb.Error:
        pass


def log_event(agent_id: str, event: str, detail: str = "") -> None:
    if _read_only:
        return
    conn = get_connection()
    with _lock:
        conn.execute(
            "INSERT INTO connection_logs (ts, agent_id, event, detail) VALUES (?, ?, ?, ?)",
            [_now(), agent_id, event, detail],
        )


def register_agent(
    agent_id: str,
    hostname: str,
    os_name: str,
    arch: str,
    version: str,
    remote_addr: str,
    interfaces: list | None = None,
) -> None:
    if _read_only:
        return
    conn = get_connection()
    now = _now()
    ifaces_json = json.dumps(interfaces or [])
    with _lock:
        exists = conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", [agent_id]
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE agents SET
                    hostname = ?, os = ?, arch = ?, version = ?,
                    status = 'online', last_seen = ?, remote_addr = ?, interfaces = ?
                WHERE agent_id = ?
                """,
                [hostname, os_name, arch, version, now, remote_addr, ifaces_json, agent_id],
            )
        else:
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, hostname, os, arch, version, status,
                     first_seen, last_seen, remote_addr, interfaces)
                VALUES (?, ?, ?, ?, ?, 'online', ?, ?, ?, ?)
                """,
                [agent_id, hostname, os_name, arch, version, now, now, remote_addr, ifaces_json],
            )


def touch_agent(agent_id: str) -> None:
    if _read_only:
        return
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE agents SET last_seen = ?, status = 'online' WHERE agent_id = ?",
            [_now(), agent_id],
        )


def mark_offline(agent_id: str) -> None:
    if _read_only:
        return
    conn = get_connection()
    with _lock:
        conn.execute(
            "UPDATE agents SET status = 'offline' WHERE agent_id = ?", [agent_id]
        )


def mark_stale_offline(timeout_seconds: float) -> list[str]:
    """Marca offline a los agentes 'online' sin actividad reciente.

    Un agente se considera muerto si su ultimo `last_seen` (refrescado en cada
    ping/registro) es mas antiguo que `timeout_seconds`. Devuelve los agent_id
    que acaban de pasar a offline, para poder registrarlos en el log.

    Se ejecuta desde un barrido periodico del servidor WebSocket. Es seguro
    llamarla desde cualquier hilo: toda la operacion se serializa con `_lock`.
    """
    if _read_only:
        return []
    conn = get_connection()
    cutoff = _now() - timedelta(seconds=timeout_seconds)
    with _lock:
        stale = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'online' AND last_seen < ?",
            [cutoff],
        ).fetchall()
        if stale:
            conn.execute(
                "UPDATE agents SET status = 'offline' WHERE status = 'online' AND last_seen < ?",
                [cutoff],
            )
    return [row[0] for row in stale]


def get_agents_df() -> pd.DataFrame:
    conn = get_connection()
    with _lock:
        return conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetch_df()


def get_agent_interfaces(agent_id: str) -> list:
    conn = get_connection()
    with _lock:
        row = conn.execute(
            "SELECT interfaces FROM agents WHERE agent_id = ?", [agent_id]
        ).fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return []


def get_logs_df(limit: int = 200) -> pd.DataFrame:
    conn = get_connection()
    with _lock:
        return conn.execute(
            "SELECT * FROM connection_logs ORDER BY ts DESC LIMIT ?", [limit]
        ).fetch_df()


# --- rutas ----------------------------------------------------------------

def add_route(cidr: str, agent_id: str) -> None:
    if _read_only:
        return
    conn = get_connection()
    with _lock:
        conn.execute("DELETE FROM routes WHERE cidr = ?", [cidr])
        conn.execute(
            "INSERT INTO routes (cidr, agent_id, status, created_at) VALUES (?, ?, 'active', ?)",
            [cidr, agent_id, _now()],
        )


def remove_route(cidr: str) -> None:
    if _read_only:
        return
    conn = get_connection()
    with _lock:
        conn.execute("UPDATE routes SET status = 'removed' WHERE cidr = ?", [cidr])


def get_routes_df() -> pd.DataFrame:
    conn = get_connection()
    with _lock:
        return conn.execute(
            "SELECT * FROM routes WHERE status = 'active' ORDER BY created_at DESC"
        ).fetch_df()
