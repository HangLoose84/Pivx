#!/bin/bash
echo "[*] Preparando el entorno para Pivx Server..."
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo ""
echo "[+] ¡Instalación completa!"
echo "[+] Para arrancar el C2 con los privilegios necesarios (TUN), ejecuta desde la raíz del proyecto:"
echo "    sudo server/venv/bin/streamlit run server/app.py"
