# Crafty Controller Wake Proxy ⚡💤

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?logo=python&logoColor=white)](#)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue.svg?logo=docker&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#)
[![Crafty Controller](https://img.shields.io/badge/Crafty%20Controller-v4-orange.svg)](#)

> 🌐 **¿Prefieres leer esto en español?** → [README en Español disponible aquí](https://github.com/thanatos84/Crafty-Controller-Wake-Proxy/blob/main/README_ES.md)

A lightweight TCP proxy microservice written in Python 3.11 and dockerized, specifically designed for Minecraft servers managed via the **Crafty Controller 4** web panel.

This proxy acts as an intelligent "Wake-on-Ping" bridge. It automatically and interactively starts the Minecraft server when a player attempts to connect (or refreshes the server list) and safely shuts it down after a configurable period of inactivity. Ideal for home hosts, low-resource VPS, dedicated servers, or 24/7 NAS setups (Synology, TrueNAS, etc.) to optimize CPU and RAM utilization.

> 📖 **Prefer reading in PDF?** You can download the complete manual ready to print at: **[docs/Crafty_Controller_Wake_Proxy_Manual_EN.pdf](docs/Crafty_Controller_Wake_Proxy_Manual_EN.pdf)**.

---

## 📋 Table of Contents
1. [How It Works](#how-it-works)
2. [Flow Diagram](#flow-diagram)
3. [Network Scheme and Ports](#network-scheme-and-ports)
4. [Obtaining Credentials in Crafty 4](#obtaining-credentials-in-crafty-4)
5. [Project Structure](#project-structure)
6. [Configuration (.env)](#configuration-env)
7. [Source Code](#source-code)
8. [Deployment and Installation Guide](#deployment-and-installation-guide)
9. [Useful Management Commands](#useful-management-commands)
10. [Best Practices and Usage Tips](#best-practices-and-usage-tips)
11. [Frequently Asked Questions (FAQ)](#faq)
12. [License](#license)

---

<a id="how-it-works"></a>

## ⚙️ How It Works

### 1. On-Demand Activation (Wake-on-Ping with Handshake Filter)
* **Active Listening:** The proxy listens on a public port (e.g., `25600` or the default Minecraft port `25565`).
* **Smart Anti-Ping Filter (Minecraft Handshake Parser):** Unlike basic TCP proxies, this proxy analyzes the Minecraft protocol in real time. It detects if the connection is a simple "status ping" (when the player is just looking at the multiplayer server list menu) or a real connection attempt (Login Request).
  * If it is a status ping or random scan and the server is offline, the proxy **instantly closes the connection and does NOT start the server**.
  * The proxy will **only send the start command to Crafty when a player actively clicks "Join Server"** (which sends a Login Request, `next_state = 2`).
* **Status Query:** Upon detecting a real login attempt, the proxy immediately queries the Minecraft server's current status via the Crafty Controller 4 REST API (`GET /api/v2/servers/{serverID}/stats`).
* **Smart Boot:** If the server is offline (`running: false`), it sends a boot command `POST /api/v2/servers/{serverID}/action/start_server` authorized with a Bearer JWT Token.
* **Wait and Forward:** The proxy asynchronously waits until Crafty confirms the server is online. Once verified, it bidirectionally links the TCP sockets, sends the cached initial Handshake packet, and transparently forwards all player traffic to the actual internal port of the Minecraft server (e.g., `25602`).

### 2. Auto-Sleep / Auto-Shutdown due to Inactivity (Double-Layered Verification)
* **Smart Inactivity Monitoring:** A secondary thread inside the proxy periodically checks active connections. To avoid false positives, **server list auto-pings and port scans do not reset the inactivity timer**. The timer only starts counting when the last real player leaves the server. Additionally, the proxy continuously monitors the server state in Crafty and, as soon as it detects the server transitioning from OFFLINE to ONLINE —whether started manually from the Crafty panel or automatically by the proxy—, **automatically resets the inactivity timer**. This guarantees a full grace period (equal to `IDLE_TIMEOUT_SECONDS` configured in the `.env` file) before the proxy can consider shutting down the server, preventing an immediate shutdown after a manual start.
* **Double-Layered Security Check:** Once the inactivity timeout (`IDLE_TIMEOUT_SECONDS`) expires, the proxy performs a double-check: it queries the Crafty REST API (`GET /api/v2/servers/{serverID}/stats`) to fetch the actual online player count (`online`). The server **will only shut down if there are 0 active TCP connections on the proxy AND the Crafty API confirms 0 online players**.
* **Resource Release:** If both conditions are met, the proxy sends a secure `POST /api/v2/servers/{serverID}/action/stop_server` request to the Crafty API, which safely saves the Minecraft world and stops the Java Virtual Machine (JVM), immediately freeing up 4GB to 16GB+ of RAM on the host system.

### 3. Secure SSL Handling
* The script gracefully and tolerantly handles Crafty's self-signed SSL certificates. It uses `urllib3.disable_warnings` to prevent security warning spam in the Docker container logs, maintaining stable and fluid HTTPS communication.

---

## 🛡️ Advanced Security and Protection Systems

To ensure that the Minecraft server only consumes system resources when a real player actively wants to play, the microservice implements two specific security systems designed to prevent accidental boot-ups and involuntary shutdowns:

### 1. Smart Handshake Filter (Minecraft Handshake Parser)
This filter analyzes the first bytes of all incoming TCP connections to the proxy's public port, decoding the official Minecraft communication protocol:
* **How it works:** When a client establishes a connection, the proxy parses the initial packet (Handshake) and reads the `next_state` field (represented as a VarInt in the protocol).
  * If `next_state = 1` (Status Ping): This indicates the client is only querying status (e.g., refreshing the multiplayer server list menu to check ping or the MOTD) or it is an automated internet port scanner. If the server is offline, **the proxy immediately closes the connection and does NOT start the server**.
  * If `next_state = 2` (Login Request): This indicates the player has actively clicked **"Join Server"**. Only in this case does the proxy proceed to wake up the server in Crafty Controller.
* **Benefit:** Prevents the server from booting up due to background port scanners, crawlers, or simply leaving the Minecraft game open on the multiplayer menu.

### 2. Double-Layered Shutdown Verification (Inactivity Double Check)
This system acts as a double-confirmation shield before proceeding with the safe shutdown of the server instance:
* **How it works:** When the inactivity timer (`IDLE_TIMEOUT_SECONDS`, e.g., 10 minutes) expires because there are 0 active TCP connections on the proxy, the microservice does not stop the server immediately. Instead, it queries the Crafty Controller REST API (`GET /api/v2/servers/{serverID}/stats`) and verifies the actual online player count (`online`) reported by the panel itself.
* **Manual start respected:** The proxy continuously monitors the Crafty server state. The moment it detects the server transition from OFFLINE to ONLINE —whether triggered manually from the Crafty panel or automatically by the proxy—, **it resets the inactivity timer automatically**. This ensures a full grace period equal to `IDLE_TIMEOUT_SECONDS` (configured in `.env`) is always observed from the moment the server boots, preventing it from being shut down immediately after a manual start.
* **Special Cases Protected:**
  * **Looping Pings:** If a player has the server in their multiplayer list, their game client will send periodic status pings that the proxy forwards. Using the handshake filter, these pings will not reset the inactivity timer.
  * **Ghost Connections or Network Glitches:** If due to a local network sync issue the proxy registers 0 connections but a player is still inside the game, the Crafty API will report `online > 0`, which will reset the inactivity timer and prevent an accidental shutdown.
  * **Safe Stop:** The shutdown command is only sent when the proxy registers 0 TCP connections **AND** the Crafty API cross-confirms that there are 0 online players in the server.

---

<a id="flow-diagram"></a>

## 🗺️ Flow Diagram

The following diagram illustrates how requests and API calls are routed between components:

```text
                  +-----------------------------------+
                  |         Minecraft Client          |
                  |     (Player in Multiplayer Tab)   |
                  +-----------------+-----------------+
                                    |
                                    | [1] TCP Connection / Ping
                                    v
                  +-----------------+-----------------+
                  |   LISTEN_PORT (public: 25600)     |
                  |   Crafty Controller Wake Proxy    |
                  +--------+-----------------+--------+
                           |                 |
     [2] Query Status      |                 | [4] Forward TCP Traffic
     (GET stats)           |                 |     (Once confirmed ONLINE)
     & Boot Command        |                 |
     (POST start_server)   |                 |
                           v                 v
            +--------------+---+     +-------+---------+
            | Crafty API       |     | Minecraft Port  |
            | (HTTPS on web    |     | (INTERNAL_PORT) |
            |  port, e.g. 8443)|     |  e.g., 25602    |
            +-------+----------+     +-------+---------+
                    |                        |
                    | Starts / Stops         | Processes game
                    v                        v
            +--------------------------------+---------+
            |          Crafty Controller 4             |
            +------------------------------------------+
```

---

<a id="network-scheme-and-ports"></a>

## 🌐 Network Scheme and Ports

To ensure a secure deployment, it is vital to understand the ports involved and which ones should be open:

* **Proxy Port (`LISTEN_PORT` - E.g., `25600` or `25565`):** 
  * *Public / External.* This is the main port listening to the internet. **This is the only Minecraft port you need to forward on your router** pointing to the local IP of the host running the proxy.
* **Internal Minecraft Port (`INTERNAL_PORT` - E.g., `25602`):**
  * *Private / Local.* This is the port configured in Crafty for your server. **DO NOT open this port on your router**. The proxy connects locally to this port to forward gameplay data.
* **Crafty URL (`CRAFTY_URL` - E.g., `https://192.168.1.100:8443`):**
  * *Private / Web Panel.* The local IP address or domain name of your Crafty Controller web admin panel (typically HTTPS on port `8443`). The proxy requires direct access to this URL to query and control the API.

---

<a id="obtaining-credentials-in-crafty-4"></a>

## 🔑 Obtaining Credentials in Crafty 4

For the proxy to communicate with the Crafty API, you must configure two key settings in the container environment variables:

### 1. Get the API Token (`API_TOKEN`)
1. Log in to your **Crafty Controller 4** web panel using an Administrator account.
2. Click on your **user icon** (located in the top-right corner) and select **Account Settings**.
3. Go to the **API Keys** section.
4. In the field *Name - What would you like to call this API Token?*, enter a descriptive name (e.g., `WakeProxy`).
5. Under the permissions checklist (*COMMANDS, TERMINAL, LOGS, SCHEDULE, BACKUP, FILES, CONFIG, PLAYERS, SERVER_CREATION, USER_CONFIG, ROLES_CONFIG, Full Access*), check the **Full Access** checkbox.
6. Click the green **+Create** button.
7. Once created, it will appear in the table at the top. Click the **Get a token** button.
8. **⚠️ CRITICAL WARNING!**: When you click it, your token will be displayed on screen. Copy and use it. **Do not click this button again**. If you click it again, Crafty will generate a new token and invalidate the one you copied previously.

### 2. Get the Server UUID (`SERVER_ID`)
1. In the Crafty panel, go to **Servers** and select the Minecraft server you want to automate.
2. Check your browser's address bar. You will see a URL like this:
   `https://crafty.your-domain.com:8443/panel/server_detail?id=9e8a7b6c-5d4e-3f2a-1b0c-9d8e7f6a5b4c`
3. The UUID is the string of alphanumeric characters after the `id=` parameter. In the example above, it is: `9e8a7b6c-5d4e-3f2a-1b0c-9d8e7f6a5b4c`. *Note: Assign this value to the `SERVER_ID` variable.*

---

<a id="project-structure"></a>

## 📁 Project Structure

The project directory structure is organized as follows to keep code and documentation clean:

```text
crafty-wake-proxy/
├── docs/
│   ├── Crafty_Controller_Wake_Proxy_Manual.pdf     # User Manual (Spanish)
│   └── Crafty_Controller_Wake_Proxy_Manual_EN.pdf  # User Manual (English)
├── src/
│   └── proxy.py                                    # Core Python execution script
├── .env.example                                    # Environment variables template
├── .gitignore                                      # Git exclusion rules
├── Dockerfile                                      # Docker image recipe
├── docker-compose.yml                              # Docker Compose orchestration
├── LICENSE                                         # MIT Open Source License
└── requirements.txt                                # Python package dependencies
```

---

<a id="configuration-env"></a>

## ⚙️ Configuration (.env)

Copy the `.env.example` file in the root of the project to create your local `.env` file and set your credentials:

```bash
cp .env.example .env
```

> ⚠️ **¡IMPORTANT!**: **DO NOT modify the source code file `src/proxy.py`**. All configurations concerning your credentials, tokens, IP addresses, and ports must be done exclusively inside the `.env` configuration file. This ensures you can update the proxy without altering the codebase and avoids accidentally pushing secrets to the internet.

Adjust the variables inside your `.env` file:

```ini
# Web administration URL of your Crafty panel (e.g. HTTPS on port 8443)
CRAFTY_URL=https://YOUR_CRAFTY_IP_OR_HOST:WEB_PORT

# API JWT Token copied in the previous step
API_TOKEN=your_api_token_here

# UUID of your Minecraft server in Crafty
SERVER_ID=your_server_uuid_here

# Host IP where Minecraft is running (typically local IP or 127.0.0.1)
TARGET_HOST=127.0.0.1

# Internal port where Minecraft runs inside Crafty (e.g. 25602)
INTERNAL_PORT=25602

# Public port players will connect to (e.g. 25600 or 25565)
LISTEN_PORT=25600

# Seconds of inactivity before stopping the server (600 = 10 minutes)
IDLE_TIMEOUT_SECONDS=600
```

---

<a id="source-code"></a>

## 💻 Source Code

The full, commented code for each of the project files is detailed below.

### 1. `src/proxy.py`
```python
import socket
import threading
import time
import requests
import urllib3
import os

# Disable warnings for Crafty's self-signed SSL certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_env():
    # Attempt to load variables from a .env file if it exists on disk (very useful for Synology DSM)
    for path in [".env", "/app/.env"]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip()
                print(f"[ENV] Loaded hot-reload configuration file: {path}", flush=True)
                break
            except Exception as e:
                print(f"[ENV ERROR] Could not read {path}: {e}", flush=True)

load_env()

# ==============================================================================
# ⚙️ CONFIGURATION SECTION (VIA ENVIRONMENT VARIABLES OR DIRECT EDIT)
# ==============================================================================

# Full URL of the Crafty Controller web panel.
CRAFTY_URL = os.environ.get("CRAFTY_URL", "https://YOUR_CRAFTY_IP_OR_HOST:WEB_PORT").strip().strip('"').strip("'")

# Crafty API Authentication Token (JWT).
API_TOKEN = os.environ.get("API_TOKEN", "PASTE_YOUR_API_TOKEN_HERE").strip().strip('"').strip("'")

# Unique Universal Identifier (UUID) of your Minecraft server in Crafty.
SERVER_ID = os.environ.get("SERVER_ID", "PASTE_YOUR_SERVER_UUID_HERE").strip().strip('"').strip("'")

# IP address or hostname where Crafty runs the Minecraft server port.
TARGET_HOST = os.environ.get("TARGET_HOST", "LOCAL_HOST_IP").strip().strip('"').strip("'")

# Internal port on which Crafty runs the Minecraft server.
try:
    INTERNAL_PORT = int(os.environ.get("INTERNAL_PORT", "25602").strip().strip('"').strip("'"))
except ValueError:
    INTERNAL_PORT = 25602

# Public/external port where this proxy listens for incoming connections.
try:
    LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "25600").strip().strip('"').strip("'"))
except ValueError:
    LISTEN_PORT = 25600

# Idle timeout (in seconds) before shutting down the server if there are no players.
try:
    IDLE_TIMEOUT_SECONDS = int(os.environ.get("IDLE_TIMEOUT_SECONDS", "600").strip().strip('"').strip("'"))
except ValueError:
    IDLE_TIMEOUT_SECONDS = 600

# ==============================================================================
# 🧠 PROXY LOGIC AND API COMMUNICATION
# ==============================================================================

active_connections = 0
last_active_time = time.time()

# Required headers to authenticate against the Crafty 4 API
headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

def is_server_running_in_crafty():
    """Queries the server status from the Crafty API."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/stats"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=4)
        if r.status_code == 200:
            data = r.json()
            # Compatible with both OpenAPI and actual response structures (data.running or data.data.running)
            running = data.get("data", {}).get("running")
            if running is None:
                running = data.get("running", False)
            return bool(running)
    except Exception as e:
        print(f"[API CHECK ERROR] Error querying status: {e}", flush=True)
    return False

def get_online_players_count():
    """Queries the Crafty API to get the number of online players."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/stats"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=4)
        if r.status_code == 200:
            data = r.json()
            # Compatible with both OpenAPI and actual response structures (data.online or data.data.online)
            online = data.get("data", {}).get("online")
            if online is None:
                online = data.get("online", 0)
            return int(online)
    except Exception as e:
        print(f"[API ERROR] Error retrieving player count: {e}", flush=True)
    return 0

def start_crafty_server():
    """Sends the boot command to the Crafty API."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/start_server"
    try:
        r = requests.post(url, headers=headers, verify=False, timeout=8)
        print(f"[API] start_server sent -> HTTP {r.status_code}: {r.text}", flush=True)
    except Exception as e:
        print(f"[API ERROR] Error requesting server boot: {e}", flush=True)

def stop_crafty_server():
    """Sends the safe shutdown command to the Crafty API."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/stop_server"
    try:
        r = requests.post(url, headers=headers, verify=False, timeout=8)
        print(f"[API] stop_server sent -> HTTP {r.status_code}", flush=True)
    except Exception as e:
        print(f"[API ERROR] Error requesting server shutdown: {e}", flush=True)

def forward(src, dst):
    """Forwards bidirectional data packets between client and Minecraft."""
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        try: src.close()
        except: pass
        try: dst.close()
        except: pass

def read_varint(data, offset):
    num = 0
    shift = 0
    while True:
        if offset >= len(data):
            return None, offset
        byte = data[offset]
        offset += 1
        num |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
        if shift >= 32:
            return None, offset
    return num, offset

def parse_handshake(data):
    try:
        offset = 0
        packet_len, offset = read_varint(data, offset)
        if packet_len is None:
            return None
        packet_id, offset = read_varint(data, offset)
        if packet_id != 0x00:
            return None
        proto_ver, offset = read_varint(data, offset)
        if proto_ver is None:
            return None
        addr_len, offset = read_varint(data, offset)
        if addr_len is None or offset + addr_len > len(data):
            return None
        offset += addr_len
        if offset + 2 > len(data):
            return None
        offset += 2
        next_state, offset = read_varint(data, offset)
        return next_state
    except Exception:
        return None

def handle_client(client_socket, addr):
    """Manages each incoming connection attempt, parsing the Minecraft protocol."""
    global active_connections, last_active_time
    active_connections += 1
    print(f"\n[PROXY] -> Connection received from {addr}", flush=True)

    is_login = False
    backend_socket = None

    try:
        # 1. Check if the server is online in Crafty
        running = is_server_running_in_crafty()
        print(f"[PROXY] Crafty status: {'ONLINE' if running else 'OFFLINE'}", flush=True)

        # 2. Read first packet to analyze the Minecraft Handshake
        client_socket.settimeout(3.0)
        data = client_socket.recv(1024)
        if not data:
            return

        next_state = parse_handshake(data)
        print(f"[PROXY] Handshake received: next_state={next_state}", flush=True)

        # If next_state == 2, it is a Login request (player clicked "Join Server")
        if next_state == 2:
            is_login = True

        if not running:
            # If offline and not a Login (e.g., server list ping or port scan), disconnect immediately
            if not is_login:
                print(f"[PROXY] Connection discarded from {addr}: Server OFFLINE and not a Login request.", flush=True)
                return

            # If offline but it is a real Login, boot up the server
            print(f"[PROXY] Player attempting to join from {addr}! Starting server in Crafty...", flush=True)
            start_crafty_server()
            print("[PROXY] Waiting for Minecraft to finish booting...", flush=True)

            # Wait up to 80 seconds for Crafty to confirm the server is online
            for _ in range(40):
                time.sleep(2)
                if is_server_running_in_crafty():
                    print("[PROXY] Server confirmed ONLINE by Crafty!", flush=True)
                    time.sleep(3)
                    break
            else:
                print("[PROXY] Server boot waiting timeout exceeded.", flush=True)
                return

        # 3. Connect to the internal Minecraft server and forward traffic
        backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_socket.connect((TARGET_HOST, INTERNAL_PORT))
        backend_socket.sendall(data)  # Forward the cached initial handshake packet

        t1 = threading.Thread(target=forward, args=(client_socket, backend_socket))
        t2 = threading.Thread(target=forward, args=(backend_socket, client_socket))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        print(f"[PROXY] Error managing client from {addr}: {e}", flush=True)
    finally:
        # Ensure sockets are closed
        try:
            client_socket.close()
        except:
            pass
        try:
            if backend_socket:
                backend_socket.close()
        except:
            pass

        # Decrement active connections count (strictly once)
        active_connections = max(0, active_connections - 1)

        # Only update the inactivity timer if it was a real gameplay session
        if is_login:
            last_active_time = time.time()
            print(f"[PROXY] <- Player {addr} disconnected. Active connections: {active_connections}", flush=True)
        else:
            print(f"[PROXY] <- Query/Ping from {addr} finished. Active connections: {active_connections}", flush=True)

def idle_checker():
    """Background thread that detects inactivity and stops the server."""
    global last_active_time
    server_was_running = is_server_running_in_crafty()

    while True:
        time.sleep(30)
        running = is_server_running_in_crafty()

        if running:
            # If server was offline and just came online (manually or auto), reset the timer
            if not server_was_running:
                last_active_time = time.time()
                server_was_running = True
                print("[PROXY] Server detected as ONLINE. Resetting inactivity timer.", flush=True)

            # Check if there are no active TCP connections on the proxy
            if active_connections == 0:
                # Check if the idle timeout threshold has passed
                if time.time() - last_active_time > IDLE_TIMEOUT_SECONDS:
                    players_online = get_online_players_count()
                    if players_online == 0:
                        print(f"[PROXY] Inactivity threshold exceeded ({IDLE_TIMEOUT_SECONDS}s without players). Shutting down server...", flush=True)
                        stop_crafty_server()
                        server_was_running = False
                    else:
                        # If there are active players reported by the API, reset the timer
                        last_active_time = time.time()
                        print(f"[PROXY] Timer reset: API reports {players_online} active players on the server.", flush=True)
        else:
            server_was_running = False

def main():
    """Starts the TCP socket server."""
    threading.Thread(target=idle_checker, daemon=True).start()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', LISTEN_PORT))
    server.listen(10)
    print(f"[PROXY] Listening on port {LISTEN_PORT}...", flush=True)

    while True:
        client, addr = server.accept()
        threading.Thread(target=handle_client, args=(client, addr)).start()

if __name__ == '__main__':
    main()
```

### 2. `Dockerfile`
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/proxy.py .
CMD ["python", "-u", "proxy.py"]
```

### 3. `docker-compose.yml`
```yaml
version: '3.8'

services:
  crafty_controller-wake_proxy:
    build: .
    container_name: crafty_controller-wake_proxy
    restart: unless-stopped
    network_mode: bridge
    ports:
      # Expose port dynamically using LISTEN_PORT environment variable (default: 25600)
      - "${LISTEN_PORT:-25600}:${LISTEN_PORT:-25600}"
    env_file:
      - .env
    volumes:
      # Relative volume mapping of the local source code script to the container
      - ./src/proxy.py:/app/proxy.py
      # Map the .env file to apply configuration changes by restarting the container
      - .env:/app/.env
```

---

<a id="deployment-and-installation-guide"></a>

## 🚀 Deployment and Installation Guide

### Method A: Standard Linux / PC / VPS (CLI)

1. **Create working directory and structure:**
   ```bash
   mkdir -p /opt/crafty_controller-wake_proxy/src
   cd /opt/crafty_controller-wake_proxy
   ```
2. **Create the files in their respective paths:**
   Upload or create the files accordingly (e.g. Python code inside `src/proxy.py`, and the template `.env.example`, `requirements.txt`, `Dockerfile`, and `docker-compose.yml` in the root).
3. **Set credentials:**
   Copy the `.env.example` file to create your `.env` file and customize the variables:
   ```bash
   cp .env.example .env
   nano .env
   ```
4. **Start the proxy:**
   Build and start the container in the background:
   ```bash
   docker compose up -d --build
   ```

---

### Method B: Synology NAS DSM / XPEnology (Container Manager / Docker UI)

If you have a Synology NAS running DSM 7.2+, the built-in **Container Manager** makes deployment very easy:

1. **Upload files to the NAS:**
   * Open **File Station**.
   * Create a folder dedicated to Docker, for example, `/volume1/docker/crafty_controller-wake_proxy`.
   * Upload the entire project structure (the `src/` folder, `Dockerfile`, `requirements.txt`, etc.).
   * Create a local `.env` file inside that directory set with your credentials.
2. **Configure the Project in DSM:**
   * Open the **Container Manager** app.
   * On the left sidebar, click the **Project** tab.
   * Click **Create**.
   * Enter a project name (e.g., `crafty_controller-wake_proxy`) and select the **Path** of your folder in File Station.
   * In **Source**, select **Create docker-compose.yml** and paste the content of the compose file from this documentation.
3. **Deploy:**
   * Proceed with the wizard.
   * In the final step, check **Build now** (Build) and initialize the project. The container will mount the local script relatively from `./src/proxy.py` and load the `.env` file automatically.

---

<a id="useful-management-commands"></a>

## 🛠️ Useful Management Commands

* **View container logs in real time:**
  ```bash
  docker logs -f crafty_controller-wake_proxy
  ```
* **Apply configuration changes (No rebuild needed):**
  Since `proxy.py` is mapped as a volume, if you modify the values in the `.env` file or the code in `src/proxy.py`, simply restart the container to apply changes:
  ```bash
  docker restart crafty_controller-wake_proxy
  ```
* **Force complete image rebuild:**
  ```bash
  docker compose down
  docker compose up -d --build
  ```
* **Stop the service temporarily:**
  ```bash
  docker compose down
  ```

---

<a id="best-practices-and-usage-tips"></a>

## 💡 Best Practices and Usage Tips

* **Recommended Inactivity Margins:** 
  Setting the `IDLE_TIMEOUT_SECONDS` variable between **600 (10 minutes) and 1800 (30 minutes)** is highly recommended. Avoid setting it lower than 5 minutes, as brief player disconnections due to network lag would shut the server down prematurely, requiring another boot wait to log back in.
* **API Token Security:**
  The `.env` contains your `API_TOKEN` which grants access to Crafty. **Ensure your local `.env` file is never uploaded to any public Git repository** (the project `.gitignore` file will handle filtering this automatically).
* **Local Port and Firewall Configuration:**
  Verify that the host firewall allows outbound communication from the proxy container to both the internal Minecraft port (`INTERNAL_PORT`) and the Crafty admin URL/port. Both must be on the same local subnet or be routable.

---

<a id="faq"></a>

## ❓ Frequently Asked Questions (FAQ)

### Will the server start due to server list pings or port scans?

**No, not at all.** The proxy implements a built-in **Smart Handshake Filter** (Minecraft Handshake Parser) specifically designed to prevent accidental wakes:
* If a player simply has the server in their multiplayer server list (auto-ping to check status) or an internet scanner probes the port, the proxy **blocks the connection** and keeps the server offline.
* The server **will only start when a player actively clicks "Join Server"**, which sends a Login Request (`next_state = 2`).

**What if it still starts on its own?**
If you still notice automatic starts, make sure no other launchers or programs are actively trying to connect/log in to the server in the background, or:
* **Use a non-standard public port**: Set a random high port in your `.env` (e.g., `LISTEN_PORT=48219`) to evade automated scanners trying to perform logins.
* **Restrict IP Addresses in the Firewall**: If you only play with friends, you can configure your firewall (or router/NAS firewall) to only accept incoming connections from your friends' IP addresses.

---

<a id="license"></a>

## 📄 License

This open-source project is distributed under the **MIT** License. You are free to use, modify, and distribute it in both personal and commercial environments. See the `LICENSE` file for more details.
