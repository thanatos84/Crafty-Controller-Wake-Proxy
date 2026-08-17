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

        # Decrementamos el contador de conexiones activas (una sola vez)
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
