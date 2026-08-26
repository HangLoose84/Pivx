#!/usr/bin/env bash
# Pivx - push inicial (Fase 2.5 + Fase 3)
# Uso:  bash push.sh
# Ejecutar desde la raiz del proyecto (donde estan agent/ y server/).
set -e

# 1) Repo (idempotente: no falla si ya existe)
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || git init -b main

# 2) Identidad (solo si no esta configurada)
git config user.name  >/dev/null 2>&1 || git config user.name  "HangLoose84"
git config user.email >/dev/null 2>&1 || git config user.email "balbontin4@gmail.com"

# 3) Preparar cambios (.gitignore ya excluye *.duckdb, *.exe, .venv/, dist/ implicito no)
git add .

# 4) Commit con mensaje detallado
git commit -F - <<'EOF'
feat: Implementación Fase 2.5 (Estabilización) y Fase 3 (MUX, L4, SOCKS5)

Fase 2.5 — Estabilización (CTF, un solo agente):
- Anti-suicidio: el agente detecta la IP/subred del uplink al C2 (conn.LocalAddr)
  y la excluye del descubrimiento de rutas, evitando bucles de enrutamiento que
  matarían la sesión.
- Timeout de sesiones: barrido keep-alive en el servidor marca 'offline' en DuckDB
  a los agentes sin pings (~45s) y expulsa su sesión en memoria (coherencia con
  las métricas de Streamlit sin esperar al cierre sucio del socket).
- Cierre TCP half-open (CloseWrite) en el proxy del netstack para no cortar de
  golpe conexiones de escaneo/servicios que hacen shutdown en un solo sentido.

Fase 3 — Suite de pivoting:
- Data-plane L3 endurecido: MTU 1350 (holgura para overhead WS), IFF_TUN|IFF_NO_PI
  documentado (evita los 4 bytes de PI que romperían el nibble de versión IP en
  gVisor) y backpressure con drop (asyncio.Queue maxsize=1000) para no bloquear el
  event loop ni agotar RAM bajo tráfico agresivo.
- Capa MUX de streams TCP sobre el mismo WebSocket, diferenciada del túnel L3 por
  el primer nibble del frame binario: 0x4/0x6 = paquete IP; 0x01 = frame MUX
  ([0x01][stream_id u32 BE][payload]). El túnel L3 queda intacto byte a byte.
- Port-forwarding L4: local (-L) y remote (-R) multiplexados sobre el mux, con
  fix de la condición de carrera de datos tempranos (registro síncrono del stream
  + buffer 'pending' que se vuelca en orden al quedar listo).
- Proxy SOCKS5 transparente (CONNECT) en 127.0.0.1:1080; la resolución DNS y el
  dial TCP ocurren en el agente (tráfico limpio para dirsearch/ffuf/Burp).
- Makefile de cross-compilación (linux/amd64, linux/arm64, windows/amd64) con
  CGO_ENABLED=0 y -ldflags="-s -w" -trimpath para binarios estáticos y ligeros.
- Documentación (README.md, ARCHITECTURE.md) actualizada: uso de forwards/SOCKS,
  diseño híbrido L3/MUX y tabla de fases.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF

# 5) Remoto (idempotente) y push
git remote get-url origin >/dev/null 2>&1 || \
  git remote add origin https://github.com/HangLoose84/Pivx.git

git push -u origin main

echo
echo "== Estado final =="
git status
