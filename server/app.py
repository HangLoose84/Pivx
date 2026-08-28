"""Pivx C2 - Monitor Tactico de Telemetria (Streamlit).

Panel de solo lectura que muestra el estado de agentes, rutas activas y logs
de conexion en tiempo real. Toda la operacion se realiza desde el Auto-Pilot CLI.
"""

from __future__ import annotations

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from pivx_server import db, runtime, ws_server

st.set_page_config(page_title="Pivx C2", page_icon="🕸️", layout="wide")


@st.cache_resource
def _boot_listener() -> str:
    started = ws_server.start_background_listener(
        host=ws_server.DEFAULT_HOST, port=ws_server.DEFAULT_PORT
    )
    return "arrancado" if started else "ya estaba activo"


boot_status = _boot_listener()
st_autorefresh(interval=3000, key="pivx_refresh")

# --- Cabecera -------------------------------------------------------------
st.title("🕸️ Pivx — Monitor Tactico")
st.caption(
    f"Listener WebSocket en `ws://{ws_server.DEFAULT_HOST}:{ws_server.DEFAULT_PORT}` "
    f"· estado: **{boot_status}** · modo: **solo lectura**"
)

agents_df = db.get_agents_df()
tun_status = runtime.status()
online_ids = tun_status["online_sessions"]

# --- Metricas -------------------------------------------------------------
online = len(online_ids)
total = 0 if agents_df.empty else len(agents_df)
active = tun_status["active_agent_id"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Agentes online", online)
c2.metric("Agentes totales", total)
c3.metric("Tunel activo", "si" if active else "no")
c4.metric("Rutas activas", len(tun_status["routes"]))

# --- Estado del tunel (informativo) ---------------------------------------
st.subheader("🔌 Estado del tunel")
if active:
    st.success(f"Tunel **{tun_status['tun_name']}** activo hacia `{active}`.")
    if tun_status["routes"]:
        st.caption("Rutas activas:")
        for cidr in tun_status["routes"]:
            st.code(f"{cidr}  ->  {tun_status['tun_name']}", language=None)
else:
    st.info("Sin tunel activo. Usa el Auto-Pilot CLI para iniciar uno.")

# --- Tabla de agentes -----------------------------------------------------
st.subheader("🖥️ Agentes")
if agents_df.empty:
    st.info("Aun no se ha conectado ningun agente.")
else:
    st.dataframe(agents_df, use_container_width=True, hide_index=True)

# --- Log de eventos -------------------------------------------------------
st.subheader("📜 Log de conexiones")
logs_df = db.get_logs_df(limit=200)
if logs_df.empty:
    st.write("Sin eventos todavia.")
else:
    st.dataframe(logs_df, use_container_width=True, hide_index=True)
