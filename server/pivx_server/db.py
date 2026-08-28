"""Capa de persistencia de Pivx sobre SQLite3.

Guarda el estado de los agentes, las subredes descubiertas, un log de eventos y
las rutas instaladas.

Usa WAL (Write-Ahead Logging) para permitir lecturas concurrentes desde
Streamlit mientras el Auto-Pilot o el servidor WebSocket escriben.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "pivx.db"

_schema_ready = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def get_connection() -> sqlite3.Connection:
    global _schema_ready
    conn = _connect()
    if not _schema_ready:
        _init_schema(conn)
        _schema_ready = True
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id    TEXT PRIMARY KEY,
            hostname    TEXT,
            os          TEXT,
            arch        TEXT,
            version     TEXT,
            status      TEXT,
            first_seen  TEXT,
            last_seen   TEXT,
            remote_addr TEXT,
            interfaces  TEXT
        );

        CREATE TABLE IF NOT EXISTS connection_logs (
            ts       TEXT,
            agent_id TEXT,
            event    TEXT,
            detail   TEXT
        );

        CREATE TABLE IF NOT EXISTS routes (
            cidr       TEXT,
            agent_id   TEXT,
            status     TEXT,
            created_at TEXT
        );
    """)
    conn.commit()


def mark_all_offline() -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE agents SET status = 'offline' WHERE status = 'online'")
        conn.commit()
    finally:
        conn.close()


def log_event(agent_id: str, event: str, detail: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO connection_logs (ts, agent_id, event, detail) VALUES (?, ?, ?, ?)",
            (_now(), agent_id, event, detail),
        )
        conn.commit()
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
        row = conn.execute(
            "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE agents SET
                    hostname = ?, os = ?, arch = ?, version = ?,
                    status = 'online', last_seen = ?, remote_addr = ?, interfaces = ?
                WHERE agent_id = ?""",
                (hostname, os_name, arch, version, now, remote_addr, ifaces_json, agent_id),
            )
        else:
            conn.execute(
                """INSERT INTO agents
                    (agent_id, hostname, os, arch, version, status,
                     first_seen, last_seen, remote_addr, interfaces)
                VALUES (?, ?, ?, ?, ?, 'online', ?, ?, ?, ?)""",
                (agent_id, hostname, os_name, arch, version, now, now, remote_addr, ifaces_json),
            )
        conn.commit()
    finally:
        conn.close()


def touch_agent(agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE agents SET last_seen = ?, status = 'online' WHERE agent_id = ?",
            (_now(), agent_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_offline(agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE agents SET status = 'offline' WHERE agent_id = ?", (agent_id,)
        )
        conn.commit()
    finally:
        conn.close()


def mark_stale_offline(timeout_seconds: float) -> list[str]:
    conn = _connect()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        rows = conn.execute(
            "SELECT agent_id FROM agents WHERE status = 'online' AND last_seen < ?",
            (cutoff,),
        ).fetchall()
        if rows:
            conn.execute(
                "UPDATE agents SET status = 'offline' WHERE status = 'online' AND last_seen < ?",
                (cutoff,),
            )
            conn.commit()
        return [r["agent_id"] for r in rows]
    finally:
        conn.close()


def get_agents_df() -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(
            "SELECT * FROM agents ORDER BY last_seen DESC", conn
        )
    finally:
        conn.close()


def get_agent_interfaces(agent_id: str) -> list:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT interfaces FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row or not row["interfaces"]:
        return []
    try:
        return json.loads(row["interfaces"])
    except (json.JSONDecodeError, TypeError):
        return []


def get_logs_df(limit: int = 200) -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(
            "SELECT * FROM connection_logs ORDER BY ts DESC LIMIT ?",
            conn, params=(limit,),
        )
    finally:
        conn.close()


# --- rutas ----------------------------------------------------------------

def add_route(cidr: str, agent_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM routes WHERE cidr = ?", (cidr,))
        conn.execute(
            "INSERT INTO routes (cidr, agent_id, status, created_at) VALUES (?, ?, 'active', ?)",
            (cidr, agent_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def remove_route(cidr: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE routes SET status = 'removed' WHERE cidr = ?", (cidr,))
        conn.commit()
    finally:
        conn.close()


def get_routes_df() -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(
            "SELECT * FROM routes WHERE status = 'active' ORDER BY created_at DESC",
            conn,
        )
    finally:
        conn.close()
