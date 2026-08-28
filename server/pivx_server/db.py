"""Capa de persistencia de Pivx sobre DuckDB.

Guarda el estado de los agentes, las subredes descubiertas, un log de eventos y
las rutas instaladas.

Arquitectura stateless: cada funcion abre su propia conexion y la cierra al
terminar. Esto evita el bloqueo exclusivo de archivo que DuckDB mantiene
mientras una conexion esta abierta, permitiendo que el Auto-Pilot y Streamlit
accedan a la misma BD sin colisionar.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "pivx.duckdb"

_schema_ready = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH))


def get_connection() -> duckdb.DuckDBPyConnection:
    global _schema_ready
    conn = _connect()
    if not _schema_ready:
        _init_schema(conn)
        _schema_ready = True
    return conn


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
    try:
        conn.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS interfaces VARCHAR")
    except duckdb.Error:
        pass


def log_event(agent_id: str, event: str, detail: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO connection_logs (ts, agent_id, event, detail) VALUES (?, ?, ?, ?)",
            [_now(), agent_id, event, detail],
        )
    finally:
        conn.close()


def register_agent(
    agent_id: str,
    hostname: str,
    os_name: str,
    arch: str,
    version: str,
    remote_addr: str,
    interfaces: list | None = None,
) -> None:
    conn = _connect()
    try:
        now = _now()
        ifaces_json = json.dumps(interfaces or [])
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
    finally:
        conn.close()


def touch_agent(agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE agents SET last_seen = ?, status = 'online' WHERE agent_id = ?",
            [_now(), agent_id],
        )
    finally:
        conn.close()


def mark_offline(agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE agents SET status = 'offline' WHERE agent_id = ?", [agent_id]
        )
    finally:
        conn.close()


def mark_stale_offline(timeout_seconds: float) -> list[str]:
    conn = _connect()
    try:
        cutoff = _now() - timedelta(seconds=timeout_seconds)
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
    finally:
        conn.close()


def get_agents_df() -> pd.DataFrame:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM agents ORDER BY last_seen DESC").fetch_df()
    finally:
        conn.close()


def get_agent_interfaces(agent_id: str) -> list:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT interfaces FROM agents WHERE agent_id = ?", [agent_id]
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return []


def get_logs_df(limit: int = 200) -> pd.DataFrame:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM connection_logs ORDER BY ts DESC LIMIT ?", [limit]
        ).fetch_df()
    finally:
        conn.close()


# --- rutas ----------------------------------------------------------------

def add_route(cidr: str, agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM routes WHERE cidr = ?", [cidr])
        conn.execute(
            "INSERT INTO routes (cidr, agent_id, status, created_at) VALUES (?, ?, 'active', ?)",
            [cidr, agent_id, _now()],
        )
    finally:
        conn.close()


def remove_route(cidr: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE routes SET status = 'removed' WHERE cidr = ?", [cidr])
    finally:
        conn.close()


def get_routes_df() -> pd.DataFrame:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM routes WHERE status = 'active' ORDER BY created_at DESC"
        ).fetch_df()
    finally:
        conn.close()
