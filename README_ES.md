# Crafty Controller Wake Proxy ⚡💤

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?logo=python&logoColor=white)](#)
[![Docker](https://img.shields.io/badge/Docker-Enabled-blue.svg?logo=docker&logoColor=white)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#)
[![Crafty Controller](https://img.shields.io/badge/Crafty%20Controller-v4-orange.svg)](#)

> 🇬🇧 **English version available here** → [README in English](https://github.com/thanatos84/Crafty-Controller-Wake-Proxy/blob/main/README.md)

Un microservicio proxy TCP ligero escrito en Python 3.11 y dockerizado, diseñado específicamente para servidores de Minecraft gestionados mediante el panel web **Crafty Controller 4**. 

Este proxy actúa como un puente inteligente "Wake-on-Ping". Levanta el servidor de Minecraft de forma automática e interactiva cuando un jugador intenta conectarse (o refresca la lista de servidores) y lo apaga de forma segura tras un periodo de inactividad configurable. Ideal para hosts domésticos, VPS de bajos recursos, servidores dedicados o NAS de uso 24/7 (Synology, TrueNAS, etc.) donde se busca optimizar el uso de CPU y memoria RAM.

> 📖 **¿Prefieres leer en PDF?** Tienes disponible el manual completo listo para descargar e imprimir en: **[docs/Crafty_Controller_Wake_Proxy_Manual.pdf](docs/Crafty_Controller_Wake_Proxy_Manual.pdf)**.

---

## 📋 Índice
1. [¿Cómo funciona?](#como-funciona)
2. [Diagrama de Flujo](#diagrama-de-flujo)
3. [Esquema de Red y Puertos](#esquema-de-red-y-puertos)
4. [Obtención de Credenciales en Crafty 4](#obtencion-credenciales)
5. [Estructura del Proyecto](#estructura-del-proyecto)
6. [Configuración (.env)](#configuracion-env)
7. [Código Fuente](#codigo-fuente)
8. [Guía de Despliegue e Instalación](#guia-de-despliegue)
9. [Comandos de Gestión Útiles](#comandos-de-gestion)
10. [Buenas Prácticas y Consejos de Uso](#buenas-practicas)
11. [Preguntas Frecuentes (FAQ)](#faq-es)
12. [Licencia](#licencia)

---

<a id="como-funciona"></a>

## ⚙️ ¿Cómo funciona?

### 1. Funcionamiento On-Demand (Wake-on-Ping con Filtro de Handshake)
* **Escucha Activa:** El proxy escucha en un puerto público (ej: `25600` o el por defecto `25565`).
* **Filtro Inteligente Anti-Ping (Minecraft Handshake Parser):** A diferencia de un proxy TCP básico, este proxy analiza en tiempo real el protocolo de Minecraft. Detecta si la conexión es un simple "ping de estado" (cuando el jugador refresca la lista de servidores en el menú) o un intento real de entrar a jugar (Login Request).
  * Si es un simple ping de lista o escaneo y el servidor está apagado, el proxy **cierra la conexión de inmediato y NO enciende el servidor**.
  * Solo si el jugador hace clic activamente en **"Entrar al servidor"** (Join Server), el proxy envía la orden de arranque a la API de Crafty.
* **Consulta de Estado:** Tras detectar un intento de entrada real, el proxy consulta inmediatamente el estado de ejecución del servidor en la API REST de Crafty Controller 4 (`GET /api/v2/servers/{serverID}/stats`).
* **Arranque Inteligente:** Si el servidor está apagado (`running: false`), envía una orden de encendido `POST /api/v2/servers/{serverID}/action/start_server` autorizada mediante un token Bearer (JWT).
* **Espera y Reenvío:** El proxy espera de forma asíncrona hasta que Crafty confirme que el servidor está online. En cuanto lo está, enlaza los sockets TCP de forma bidireccional, envía el paquete de Handshake inicial que estaba almacenado en el búfer y reenvía transparentemente todo el tráfico del jugador al puerto interno real del servidor de Minecraft (ej: `25602`).

### 2. Auto-Sleep / Auto-Shutdown por inactividad (Doble Verificación)
* **Monitorización Inteligente:** Un hilo secundario del proxy verifica periódicamente el contador de conexiones activas. Para evitar falsos positivos, **los pings de actualización de lista de servidores y escáneres de puertos no reinician el temporizador de inactividad**. El temporizador solo comienza a contar cuando el último jugador real abandona la partida. Además, el proxy monitoriza el estado del servidor en Crafty y, en cuanto detecta que el servidor ha pasado de apagado (OFFLINE) a encendido (ONLINE) —ya sea iniciado a mano en el panel o de forma automática por el proxy—, **resetea el temporizador de inactividad**, garantizando un tiempo de cortesía inicial equivalente a los segundos configurados en `IDLE_TIMEOUT_SECONDS` en el archivo `.env` antes de poder apagarlo.
* **Doble Comprobación de Seguridad:** Al expirar el tiempo de inactividad (`IDLE_TIMEOUT_SECONDS`), el proxy realiza una doble verificación: consulta la API REST de Crafty (`GET /api/v2/servers/{serverID}/stats`) para obtener el recuento real de jugadores online (`online`). El servidor **solo se apagará si hay 0 conexiones activas en el proxy Y la API de Crafty confirma que hay 0 jugadores online**.
* **Liberación de Recursos:** Si ambas condiciones son verdaderas, el proxy realiza una petición segura `POST /api/v2/servers/{serverID}/action/stop_server` a la API de Crafty, la cual guarda el mundo de Minecraft de forma segura y detiene la máquina virtual Java (JVM), liberando instantáneamente de 4GB a 16GB+ de memoria RAM en el host.


### 3. Soporte SSL Seguro
* El script maneja de manera fluida y tolerante los certificados SSL autofirmados de Crafty. Utiliza `urllib3.disable_warnings` para evitar spam de alertas de seguridad en los logs del contenedor Docker, permitiendo la comunicación local HTTPS de forma estable y fluida.

---

## 🛡️ Sistemas de Seguridad y Protección Avanzada

Para garantizar que el servidor de Minecraft solo consuma recursos cuando un jugador real quiere jugar, el microservicio implementa dos sistemas de seguridad específicos diseñados para evitar el encendido accidental o el apagado involuntario del servidor:

### 1. Filtro Inteligente de Handshake (Minecraft Handshake Parser)
Este filtro analiza los primeros bytes de todas las conexiones TCP entrantes al puerto público del proxy, decodificando el protocolo oficial de comunicación de Minecraft:
* **Cómo funciona:** Cuando un cliente realiza una petición, el proxy lee el paquete inicial (Handshake) y comprueba el campo `next_state` representado por un VarInt en el protocolo.
  * Si `next_state = 1` (Status Ping): Significa que el cliente solo está consultando el estado (por ejemplo, refrescando la lista de servidores del menú multijugador para ver la latencia o el MOTD), o que se trata de un escaneo automatizado de puertos de internet. Si el servidor está apagado, **el proxy cierra inmediatamente la conexión y no inicia el servidor**.
  * Si `next_state = 2` (Login Request): Significa que el jugador ha hecho clic de forma activa en **"Entrar al servidor" (Join Server)**. Solo en este caso el proxy procede a despertar el servidor en Crafty Controller.
* **Beneficio:** Evita que el servidor se encienda solo por bots escaneando internet o por el simple hecho de dejar el juego abierto en el menú de selección de servidores.

### 2. Doble Verificación de Apagado por API (Inactivity Double Check)
Este sistema actúa como un doble escudo de confirmación antes de proceder con el apagado seguro de la instancia:
* **Cómo funciona:** Cuando el temporizador de inactividad (`IDLE_TIMEOUT_SECONDS`, ej. 10 minutos) expira porque no hay conexiones TCP activas en el proxy, el microservicio no apaga el servidor directamente. En su lugar, realiza una llamada a la API REST de Crafty Controller (`GET /api/v2/servers/{serverID}/stats`) y verifica el recuento real de jugadores conectados (`online`) registrados por el propio panel.
* **Arranque manual respetado:** El proxy monitoriza continuamente el estado del servidor en Crafty. En el momento en que detecta que el servidor pasa de OFFLINE a ONLINE —ya sea iniciado manualmente desde el panel de Crafty o de forma automática por el proxy—, **resetea automáticamente el temporizador de inactividad**. Esto garantiza que siempre habrá un período de gracia completo (`IDLE_TIMEOUT_SECONDS`, configurado en el `.env`) antes de que el proxy pueda considerar apagarlo, evitando que se apague inmediatamente después de un arranque manual.
* **Casos especiales protegidos:**
  * **Pings en bucle:** Si un jugador tiene el servidor en su lista de multijugador, su juego enviará pings periódicos que el proxy responderá de forma transparente. Al usar el filtrado inteligente, estos pings no reiniciarán el temporizador de inactividad.
  * **Conexiones Fantasma o Desconexiones de red:** Si por un error de sincronización de red local el proxy reportara 0 conexiones pero aún quedara un jugador dentro, la API de Crafty reportará `online > 0`, lo que reiniciará el temporizador de inactividad evitando un apagado accidental.
  * **Apagado Seguro:** Solo cuando el proxy registre 0 conexiones TCP **Y** la API de Crafty confirme de forma cruzada que hay 0 jugadores en el servidor, se enviará la orden de apagado.

---

<a id="diagrama-de-flujo"></a>

## 🗺️ Diagrama de Flujo

El siguiente esquema resume cómo se enrutan las peticiones y peticiones de API entre los componentes del ecosistema:

```text
                  +-----------------------------------+
                  |        Cliente Minecraft          |
                  |     (Jugador en Multijugador)     |
                  +-----------------+-----------------+
                                    |
                                    | [1] Intento de Conexión / Ping (TCP)
                                    v
                  +-----------------+-----------------+
                  |   LISTEN_PORT (público: 25600)    |
                  |   Crafty Controller Wake Proxy    |
                  +--------+-----------------+--------+
                           |                 |
     [2] Consulta Estado   |                 | [4] Reenvío de tráfico TCP
     (GET stats)           |                 |     (Una vez confirmado ONLINE)
     & Orden de Arranque   |                 |
     (POST start_server)   |                 |
                           v                 v
            +--------------+---+     +-------+---------+
            | API de Crafty    |     | Puerto Minecraft|
            | (HTTPS en puerto |     | (INTERNAL_PORT) |
            |  web, ej: 8443)  |     |  ej: 25602      |
            +-------+----------+     +-------+---------+
                    |                        |
                    | Enciende / Apaga       | Procesa juego
                    v                        v
            +--------------------------------+---------+
            |          Crafty Controller 4             |
            +------------------------------------------+
```

---

<a id="esquema-de-red-y-puertos"></a>

## 🌐 Esquema de Red y Puertos

Para asegurar un despliegue seguro, es fundamental comprender qué puertos intervienen y cuáles deben abrirse en tu red:

* **Puerto del Proxy (`LISTEN_PORT` - Ej: `25600` o `25565`):** 
  * *Público / Externo.* Es el puerto principal que escucha internet. **Este es el único puerto de Minecraft que debes abrir en el router (Port Forwarding)** apuntando a la IP local del host donde corre el proxy.
* **Puerto Interno de Minecraft (`INTERNAL_PORT` - Ej: `25602`):**
  * *Privado / Local.* Es el puerto configurado en el servidor dentro de Crafty. **NO debe abrirse en el router**. El proxy se conectará localmente a este puerto en el host para hacer el reenvío de datos.
* **Dirección URL de Crafty (`CRAFTY_URL` - Ej: `https://192.168.1.100:8443`):**
  * *Privada / Panel Web.* Es la dirección IP local o nombre de dominio del panel web de tu instancia de Crafty Controller (normalmente HTTPS en el puerto `8443`). El proxy requiere acceso directo a esta IP para controlar la API.

---

<a id="obtencion-credenciales"></a>

## 🔑 Obtención de Credenciales en Crafty 4

Para que el proxy pueda comunicarse con la API de Crafty, debes configurar dos valores clave en las variables de entorno del contenedor:

### 1. Obtener el Token de la API (`API_TOKEN`)
1. Inicia sesión en el panel web de **Crafty Controller 4** con una cuenta de Administrador.
2. Haz clic en tu **icono de usuario** (ubicado arriba a la derecha) y selecciona **Ajustes de la cuenta** (Account Settings).
3. Dirígete a la sección de **Claves API** (API Keys).
4. En el apartado *Nombre - ¿Cómo te gustaría llamar a este Token de API?*, introduce un nombre descriptivo (ej: `WakeProxy`).
5. En la lista de permisos (*COMMANDS, TERMINAL, LOGS, SCHEDULE, BACKUP, FILES, CONFIG, PLAYERS, SERVER_CREATION, USER_CONFIG, ROLES_CONFIG, Acceso completo*), marca la casilla de **Acceso completo** (Full Access).
6. Haz clic en el botón verde **+Create**.
7. Una vez creado, se mostrará en la tabla superior. Haz clic en el botón **Conseguir un token** (Get a token).
8. **⚠️ ¡MUY IMPORTANTE!**: Al hacer clic, se mostrará tu token en pantalla. Cópialo y utilízalo. **No vuelvas a hacer clic en este botón**. Si haces clic de nuevo, Crafty generará un token nuevo e invalidará el token que copiaste anteriormente.

### 2. Obtener el UUID del Servidor (`SERVER_ID`)
1. En el panel de Crafty, ve a la sección de **Servers** (Servidores) y selecciona el servidor de Minecraft que deseas automatizar.
2. Observa la barra de direcciones de tu navegador web. Verás una URL similar a esta:
   `https://crafty.tu-dominio.com:8443/panel/server_detail?id=9e8a7b6c-5d4e-3f2a-1b0c-9d8e7f6a5b4c`
3. El UUID es la cadena de caracteres alfanuméricos después del parámetro `id=`. En el ejemplo anterior, sería: `9e8a7b6c-5d4e-3f2a-1b0c-9d8e7f6a5b4c`. *Nota: Este valor se asigna a la variable `SERVER_ID`.*

---

<a id="estructura-del-proyecto"></a>

## 📁 Estructura del Proyecto

La estructura de directorios del proyecto está organizada de la siguiente manera para mantener el código y la documentación limpios:

```text
crafty-wake-proxy/
├── docs/
│   ├── Crafty_Controller_Wake_Proxy_Manual.pdf     # Manual en Español
│   └── Crafty_Controller_Wake_Proxy_Manual_EN.pdf  # Manual en Inglés
├── src/
│   └── proxy.py                                    # Script de ejecución en Python
├── .env.example                                    # Plantilla de variables de entorno
├── .gitignore                                      # Exclusiones de Git
├── Dockerfile                                      # Receta de la imagen Docker
├── docker-compose.yml                              # Orquestación de Docker Compose
├── LICENSE                                         # Licencia MIT de código abierto
└── requirements.txt                                # Dependencias de paquetes Python
```

---

<a id="configuracion-env"></a>

## ⚙️ Configuración (.env)

Copia el archivo `.env.example` de la raíz del proyecto para crear tu archivo `.env` local e introduce tus credenciales:

```bash
cp .env.example .env
```

> ⚠️ **¡IMPORTANTE!**: **NO modifiques el archivo de código fuente `src/proxy.py`**. Toda la configuración de tus credenciales, tokens, direcciones IP y puertos debe realizarse de forma exclusiva dentro del archivo de configuración `.env`. Esto asegura que puedas actualizar el proxy sin alterar el código y evita subir tus credenciales públicas a internet de manera accidental.

Ajusta las variables dentro de tu archivo `.env`:

```ini
# URL de administración web de tu panel Crafty (ej. https://192.168.1.100:8443)
CRAFTY_URL=https://TU_IP_O_HOST_CRAFTY:PUERTO_WEB

# Token API JWT copiado en el paso anterior
API_TOKEN=tu_api_token_aqui

# UUID del servidor de Minecraft
SERVER_ID=tu_server_uuid_aqui

# IP del host donde corre Minecraft (típicamente la IP local del host o 127.0.0.1)
TARGET_HOST=127.0.0.1

# Puerto interno en el que Minecraft corre dentro de Crafty (ej. 25602)
INTERNAL_PORT=25602

# Puerto público al que se conectarán los jugadores (ej. 25600 o 25565)
LISTEN_PORT=25600

# Segundos de inactividad antes de apagar el servidor (600 = 10 minutos)
IDLE_TIMEOUT_SECONDS=600
```

---

<a id="codigo-fuente"></a>

## 💻 Código Fuente

A continuación se detalla el código completo y listo para cada uno de los archivos del proyecto.

### 1. `src/proxy.py`
```python
import socket
import threading
import time
import requests
import urllib3
import os

# Desactiva las advertencias por certificados SSL autofirmados de Crafty
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def load_env():
    # Intenta cargar variables de un archivo .env si existe en disco (muy util para Synology DSM)
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
                print(f"[ENV] Cargado archivo de configuracion local en caliente: {path}", flush=True)
                break
            except Exception as e:
                print(f"[ENV ERROR] No se pudo leer {path}: {e}", flush=True)

load_env()

# ==============================================================================
# ⚙️ SECCIÓN DE CONFIGURACIÓN (VÍA VARIABLES DE ENTORNO O EDICIÓN DIRECTA)
# ==============================================================================

# URL completa del panel web de Crafty Controller.
CRAFTY_URL = os.environ.get("CRAFTY_URL", "https://TU_IP_O_HOST_CRAFTY:PUERTO_WEB").strip().strip('"').strip("'")

# Token de autenticación de la API de Crafty (JWT).
API_TOKEN = os.environ.get("API_TOKEN", "PEGA_AQUI_TU_API_TOKEN").strip().strip('"').strip("'")

# Identificador único universal (UUID) de tu servidor de Minecraft en Crafty.
SERVER_ID = os.environ.get("SERVER_ID", "PEGA_AQUI_TU_SERVER_UUID").strip().strip('"').strip("'")

# Dirección IP o hostname donde Crafty tiene escuchando el puerto del servidor Minecraft.
TARGET_HOST = os.environ.get("TARGET_HOST", "IP_LOCAL_DEL_HOST").strip().strip('"').strip("'")

# Puerto interno en el que Crafty ejecuta el servidor de Minecraft.
try:
    INTERNAL_PORT = int(os.environ.get("INTERNAL_PORT", "25602").strip().strip('"').strip("'"))
except ValueError:
    INTERNAL_PORT = 25602

# Puerto público/externo donde este proxy escucha las peticiones entrantes.
try:
    LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "25600").strip().strip('"').strip("'"))
except ValueError:
    LISTEN_PORT = 25600

# Tiempo de inactividad (en segundos) antes de apagar el servidor si no hay jugadores.
try:
    IDLE_TIMEOUT_SECONDS = int(os.environ.get("IDLE_TIMEOUT_SECONDS", "600").strip().strip('"').strip("'"))
except ValueError:
    IDLE_TIMEOUT_SECONDS = 600

# ==============================================================================
# 🧠 LÓGICA DEL PROXY Y COMUNICACIÓN CON LA API
# ==============================================================================

active_connections = 0
last_active_time = time.time()

# Cabeceras requeridas para autenticarse contra la API de Crafty 4
headers = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

def is_server_running_in_crafty():
    """Consulta el estado del servidor a la API de Crafty."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/stats"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=4)
        if r.status_code == 200:
            data = r.json()
            # Compatible con estructura OpenAPI y real (data.running o data.data.running)
            running = data.get("data", {}).get("running")
            if running is None:
                running = data.get("running", False)
            return bool(running)
    except Exception as e:
        print(f"[API CHECK ERROR] Error consultando estado: {e}", flush=True)
    return False

def get_online_players_count():
    """Consulta la API de Crafty para obtener el número de jugadores online."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/stats"
    try:
        r = requests.get(url, headers=headers, verify=False, timeout=4)
        if r.status_code == 200:
            data = r.json()
            # Compatible con estructura OpenAPI y real (data.online o data.data.online)
            online = data.get("data", {}).get("online")
            if online is None:
                online = data.get("online", 0)
            return int(online)
    except Exception as e:
        print(f"[API ERROR] Error obteniendo recuento de jugadores: {e}", flush=True)
    return 0

def start_crafty_server():
    """Envía la orden de arranque a la API de Crafty."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/start_server"
    try:
        r = requests.post(url, headers=headers, verify=False, timeout=8)
        print(f"[API] start_server enviado -> HTTP {r.status_code}: {r.text}", flush=True)
    except Exception as e:
        print(f"[API ERROR] Error al solicitar arranque: {e}", flush=True)

def stop_crafty_server():
    """Envía la orden de apagado seguro a la API de Crafty."""
    url = f"{CRAFTY_URL}/api/v2/servers/{SERVER_ID}/action/stop_server"
    try:
        r = requests.post(url, headers=headers, verify=False, timeout=8)
        print(f"[API] stop_server enviado -> HTTP {r.status_code}", flush=True)
    except Exception as e:
        print(f"[API ERROR] Error al solicitar apagado: {e}", flush=True)

def forward(src, dst):
    """Reenvía paquetes de datos bidireccionales entre el cliente y Minecraft."""
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
    """Gestiona cada intento de conexión entrante, analizando el protocolo de Minecraft."""
    global active_connections, last_active_time
    active_connections += 1
    print(f"\n[PROXY] -> Petición recibida desde {addr}", flush=True)

    is_login = False
    backend_socket = None

    try:
        # 1. Comprobar si el servidor está encendido en Crafty
        running = is_server_running_in_crafty()
        print(f"[PROXY] Estado en Crafty: {'ONLINE' if running else 'OFFLINE'}", flush=True)

        # 2. Leer primer paquete para analizar el Handshake de Minecraft
        client_socket.settimeout(3.0)
        data = client_socket.recv(1024)
        if not data:
            return

        next_state = parse_handshake(data)
        print(f"[PROXY] Handshake recibido: next_state={next_state}", flush=True)

        # Si next_state == 2, es una petición de Login (el jugador pulsó "Join Server")
        if next_state == 2:
            is_login = True

        if not running:
            # Si está apagado y no es un Login (es un ping de lista o un escaneo de puertos), desconectamos de inmediato
            if not is_login:
                print(f"[PROXY] Conexión descartada desde {addr}: Servidor OFFLINE y no es petición de Login.", flush=True)
                return

            # Si está apagado pero es un Login real, encendemos el servidor
            print(f"[PROXY] ¡Jugador intentando entrar desde {addr}! Iniciando servidor en Crafty...", flush=True)
            start_crafty_server()
            print("[PROXY] Esperando a que Minecraft termine de iniciar...", flush=True)

            # Espera hasta 80 segundos a que Crafty confirme que el servidor está online
            for _ in range(40):
                time.sleep(2)
                if is_server_running_in_crafty():
                    print("[PROXY] ¡Servidor confirmado ONLINE por Crafty!", flush=True)
                    time.sleep(3)
                    break
            else:
                print("[PROXY] Tiempo de espera de inicio agotado.", flush=True)
                return

        # 3. Conectar al servidor interno de Minecraft y reenviar el tráfico
        backend_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_socket.connect((TARGET_HOST, INTERNAL_PORT))
        backend_socket.sendall(data)  # Reenviar el paquete inicial de handshake leído

        t1 = threading.Thread(target=forward, args=(client_socket, backend_socket))
        t2 = threading.Thread(target=forward, args=(backend_socket, client_socket))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    except Exception as e:
        print(f"[PROXY] Error gestionando cliente desde {addr}: {e}", flush=True)
    finally:
        # Aseguramos el cierre de los sockets
        try:
            client_socket.close()
        except:
            pass
        try:
            if backend_socket:
                backend_socket.close()
        except:
            pass

        # Decremento del contador de conexiones activas (estrictamente una sola vez)
        active_connections = max(0, active_connections - 1)

        # Solo actualizamos el temporizador de inactividad si fue una sesión de juego real
        if is_login:
            last_active_time = time.time()
            print(f"[PROXY] <- Jugador {addr} desconectado. Conexiones activas: {active_connections}", flush=True)
        else:
            print(f"[PROXY] <- Consulta/Ping desde {addr} finalizado. Conexiones activas: {active_connections}", flush=True)

def idle_checker():
    """Hilo en segundo plano que detecta inactividad y apaga el servidor."""
    global last_active_time
    server_was_running = is_server_running_in_crafty()

    while True:
        time.sleep(30)
        running = is_server_running_in_crafty()

        if running:
            # Si el servidor estaba apagado y se enciende (manual o automáticamente), reseteamos temporizador
            if not server_was_running:
                last_active_time = time.time()
                server_was_running = True
                print("[PROXY] Servidor detectado como ONLINE. Reseteando temporizador de inactividad.", flush=True)

            # Comprobar si no hay conexiones TCP activas en el proxy
            if active_connections == 0:
                # Comprobar si ha transcurrido el tiempo límite de inactividad
                if time.time() - last_active_time > IDLE_TIMEOUT_SECONDS:
                    players_online = get_online_players_count()
                    if players_online == 0:
                        print(f"[PROXY] Inactividad superada ({IDLE_TIMEOUT_SECONDS}s sin jugadores). Apagando servidor...", flush=True)
                        stop_crafty_server()
                        server_was_running = False
                    else:
                        # Si hay jugadores activos en el servidor, reseteamos el temporizador
                        last_active_time = time.time()
                        print(f"[PROXY] Temporizador reseteado: la API reporta {players_online} jugadores activos en el servidor.", flush=True)
        else:
            server_was_running = False

def main():
    """Inicio del servidor socket TCP."""
    threading.Thread(target=idle_checker, daemon=True).start()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', LISTEN_PORT))
    server.listen(10)
    print(f"[PROXY] Escuchando en el puerto {LISTEN_PORT}...", flush=True)

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
      # El puerto expuesto corresponde al configurado en la variable LISTEN_PORT (por defecto 25600)
      - "${LISTEN_PORT:-25600}:${LISTEN_PORT:-25600}"
    env_file:
      - .env
    volumes:
      # Mapea el script local al contenedor de forma relativa
      - ./src/proxy.py:/app/proxy.py
      # Mapea el archivo .env para aplicar cambios en caliente reiniciando el contenedor
      - .env:/app/.env
```

---

<a id="guia-de-despliegue"></a>

## 🚀 Guía de Despliegue e Instalación

### Método A: Linux / PC / VPS Estándar (CLI)

1. **Crear el directorio y la estructura básica:**
   ```bash
   mkdir -p /opt/crafty_controller-wake_proxy/src
   cd /opt/crafty_controller-wake_proxy
   ```
2. **Crear los archivos en su respectiva ruta:**
   Sube o crea los archivos correspondientes en su lugar (ej. el código python dentro de `src/proxy.py`, y la plantilla `.env.example`, `requirements.txt`, `Dockerfile` y `docker-compose.yml` en la raíz).
3. **Establecer credenciales:**
   Copia el archivo `.env.example` para crear tu `.env` de configuración y edita las credenciales:
   ```bash
   cp .env.example .env
   nano .env
   ```
4. **Iniciar el proxy:**
   Compila y levanta el contenedor en segundo plano:
   ```bash
   docker compose up -d --build
   ```

---

### Método B: Synology DSM / XPEnology (Container Manager / Docker UI)

Si tienes un servidor NAS de Synology con DSM 7.2+, la herramienta integrada **Container Manager** facilita enormemente la gestión:

1. **Subir archivos al NAS:**
   * Abre **File Station**.
   * Crea una carpeta en la raíz dedicada a Docker, por ejemplo, `/volume1/docker/crafty_controller-wake_proxy`.
   * Sube toda la estructura del proyecto (carpeta `src/`, `Dockerfile`, `requirements.txt`, etc.).
   * Crea un archivo `.env` local en dicha carpeta configurado con tus credenciales.
2. **Configurar el Proyecto en DSM:**
   * Abre la aplicación **Container Manager**.
   * En la barra lateral izquierda, selecciona la pestaña **Proyecto**.
   * Haz clic en **Crear**.
   * Asigna un nombre al proyecto (ej: `crafty_controller-wake_proxy`) y selecciona la **Ruta** de tu carpeta en File Station.
   * En **Origen**, selecciona **Crear docker-compose.yml** y pega el contenido del archivo detallado en esta documentación.
3. **Desplegar:**
   * Continúa con el asistente de Synology.
   * En el último paso, selecciona **Compilar ahora** (Build) e iniciar el servicio. El contenedor montará el script localmente de forma relativa `./src/proxy.py` y cargará el archivo de entorno `.env` de forma automática.

---

<a id="comandos-de-gestion"></a>

## 🛠️ Comandos de Gestión Útiles

* **Visualizar los logs en tiempo real:**
  ```bash
  docker logs -f crafty_controller-wake_proxy
  ```
* **Aplicar cambios en la configuración (Sin recompilar):**
  Dado que `proxy.py` está enlazado mediante volúmenes, si modificas los valores en el archivo `.env` o el código en `src/proxy.py`, solo tienes que reiniciar el contenedor para aplicar los cambios:
  ```bash
  docker restart crafty_controller-wake_proxy
  ```
* **Forzar la reconstrucción total de la imagen:**
  ```bash
  docker compose down
  docker compose up -d --build
  ```
* **Detener el servicio temporalmente:**
  ```bash
  docker compose down
  ```

---

<a id="buenas-practicas"></a>

## 💡 Buenas Prácticas y Consejos de Uso

* **Tiempos de inactividad recomendados:** 
  Establecer la variable `IDLE_TIMEOUT_SECONDS` a un rango de entre **600 (10 minutos) a 1800 (30 minutos)** es el balance perfecto. Evita configurarlo en valores menores a 5 minutos, ya que podría apagar el servidor si un jugador sufre una desconexión breve, requiriendo esperar de nuevo todo el proceso de inicio para volver a entrar.
* **Seguridad y Token API:**
  El `.env` contiene tu `API_TOKEN` que permite el acceso a Crafty. **Asegúrate de que tu archivo `.env` local no se suba a ningún repositorio Git** (el `.gitignore` del proyecto se encargará de filtrarlo automáticamente).
* **Control de puertos locales:**
  Asegúrate de que la máquina donde ejecutas el proxy no tenga bloqueada la comunicación hacia el puerto interno real de Minecraft (`INTERNAL_PORT`) ni hacia el panel de Crafty. Ambos deben estar bajo la misma subred local o ser enrutables.

---

<a id="faq-es"></a>

## ❓ Preguntas Frecuentes (FAQ)

### ¿Se encenderá el servidor con pings de la lista de servidores o escaneos de puertos?

**No, en absoluto.** El proxy implementa un **Filtro Inteligente de Handshake** (Minecraft Handshake Parser) diseñado específicamente para evitar encendidos accidentales:
* Si un jugador simplemente tiene el servidor en su lista de multijugador (ping automático para ver el estado) o un escáner de internet sondea el puerto, el proxy **bloquea la conexión** y mantiene el servidor apagado.
* El servidor **únicamente se encenderá cuando un jugador haga clic en "Entrar al servidor" (Join Server)**, lo que envía una petición de login real (`next_state = 2`).

**¿Qué pasa si sigue encendiéndose solo?**
Si aún notas encendidos automáticos, asegúrate de que no haya otros programas o launchers que intenten loguearse activamente al servidor en segundo plano, o bien:
* **Usa un puerto no estándar**: Configura un puerto aleatorio en el `.env` (ej: `LISTEN_PORT=48219`) para evadir bots que intenten hacer logins de fuerza bruta.
* **Restricción de IPs en el cortafuegos**: Si juegas únicamente con amigos, puedes configurar tu cortafuegos (o el firewall de tu router/NAS) para que solo acepte conexiones entrantes al puerto del proxy desde las direcciones IP de tus amigos.

---

<a id="licencia"></a>

## 📄 Licencia

Este proyecto está bajo la Licencia **MIT**. Puedes usarlo, modificarlo y distribuirlo libremente tanto en entornos personales como comerciales. Consulta el archivo `LICENSE` en este repositorio para más detalles.
