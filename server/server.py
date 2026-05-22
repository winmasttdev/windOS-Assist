import asyncio
import json
import os
import sys
import socket
import struct
import time
import datetime
import math
import urllib.request
import logging
from threading import Thread

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("windOS-Server")

# Optional dependency imports with fallbacks
try:
    import websockets
except ImportError:
    logger.error("Required package 'websockets' not found. Please install via: pip install websockets")
    sys.exit(1)

try:
    import telebot
    from telebot.async_telebot import AsyncTeleBot
    from telebot import types
except ImportError:
    logger.error("Required package 'pyTelegramBotAPI' not found. Please install via: pip install pyTelegramBotAPI")
    sys.exit(1)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
    logger.warning("Package 'openai' not found. AI assistant feature will fall back to mock mode unless installed via: pip install openai")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_config.json")

# Server state
config = {}
connected_clients = {}  # client_id: {websocket, name, mac, coords, hardware, voltage, uptime, last_seen}
active_client_id = None
terminal_sessions = {}  # chat_id: client_id (active terminal session)
ai_sessions = {}        # chat_id: True (active continuous AI chat mode)
ai_history = {}         # chat_id: list of messages

# Load configuration
def load_config():
    global config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
    else:
        logger.warning(f"Configuration file not found at {CONFIG_PATH}. Running with defaults.")
        config = {
            "telegram_token": "",
            "authorized_chat_id": 0,
            "websocket_port": 8765,
            "websocket_host": "0.0.0.0",
            "client_token": "windos_secret_token",
            "ai_provider": "google",  # google, openai, ollama, custom
            "ai_api_key": "",
            "ai_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "ai_model": "gemini-1.5-flash",
            "server_mac": "00:00:00:00:00:00"
        }

load_config()

# Helper: Save config back
def save_config():
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        logger.info("Configuration saved.")
    except Exception as e:
        logger.error(f"Error saving configuration: {e}")

# Helper: Fetch server local hardware info
def get_server_hardware():
    cpu = "Unknown Linux CPU"
    gpu = "Unknown Linux GPU"
    
    # Try reading CPU info
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        cpu = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
            
        # Try getting GPU info
        try:
            # Check lspci for VGA
            import subprocess
            res = subprocess.check_output("lspci | grep -i -E 'vga|3d|display'", shell=True).decode()
            gpus = []
            for line in res.strip().split("\n"):
                if ":" in line:
                    gpus.append(line.split(":", 2)[-1].strip())
            if gpus:
                gpu = ", ".join(gpus)
        except Exception:
            pass
            
    return {"cpu": cpu, "gpu": gpu}

# Helper: Get server uptime
def get_server_uptime():
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                return str(datetime.timedelta(seconds=int(uptime_seconds)))
        except Exception:
            pass
    return "Unknown Uptime"

# Helper: Get server voltage / battery status
def get_server_voltage():
    if sys.platform.startswith("linux"):
        # Look for hwmon sensors
        try:
            for i in range(10):
                path = f"/sys/class/hwmon/hwmon{i}/in0_input"
                if os.path.exists(path):
                    with open(path, "r") as f:
                        volts = float(f.read().strip()) / 1000.0
                        return f"{volts:.2f}V"
        except Exception:
            pass
        # Fallback to battery design voltage
        try:
            path = "/sys/class/power_supply/BAT0/voltage_now"
            if os.path.exists(path):
                with open(path, "r") as f:
                    volts = float(f.read().strip()) / 1000000.0
                    return f"{volts:.2f}V (Battery)"
        except Exception:
            pass
    return "N/A"

# Helper: Geolocation via IP-API (Stable and keyless)
def fetch_ip_geolocation():
    try:
        req = urllib.request.Request("http://ip-api.com/json/", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if data.get("status") == "success":
                return {
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                    "city": data.get("city"),
                    "country": data.get("country")
                }
    except Exception as e:
        logger.warning(f"IP-based geolocation lookup failed: {e}")
    # Fallback to Kyiv coordinates
    return {"lat": 50.4501, "lon": 30.5234, "city": "Kyiv", "country": "Ukraine"}

# Global server geo info
server_geo = fetch_ip_geolocation()

# Helper: Haversine distance calculator
def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0 # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Helper: Open-Meteo Weather API
def fetch_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            current = data.get("current_weather", {})
            temp = current.get("temperature")
            wind = current.get("windspeed")
            code = current.get("weathercode")
            # Weather code translator
            codes = {
                0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
                61: "Light rain", 63: "Moderate rain", 65: "Heavy rain", 71: "Light snow",
                73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains", 80: "Light showers",
                81: "Moderate showers", 82: "Violent showers", 95: "Thunderstorm"
            }
            desc = codes.get(code, "Unknown Weather")
            return f"{temp}°C, {desc} (Wind: {wind} km/h)"
    except Exception as e:
        logger.warning(f"Weather lookup failed: {e}")
    return "Unknown Weather (API failure)"

# Helper: Wake on LAN
def send_wake_on_lan(mac_address):
    try:
        # Format MAC address
        if len(mac_address) == 17:
            sep = mac_address[2]
            mac_address = mac_address.replace(sep, "")
        elif len(mac_address) != 12:
            raise ValueError("Incorrect MAC address format")
            
        # Create magic packet
        data = bytes.fromhex("F" * 12 + mac_address * 16)
        
        # Broadcast magic packet
        soc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        soc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        soc.sendto(data, ('255.255.255.255', 9))
        soc.close()
        logger.info(f"WoL Magic Packet sent to {mac_address}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Wake-on-Lan packet: {e}")
        return False

# Initialize Telegram Bot
bot = AsyncTeleBot(config.get("telegram_token", ""))

# Decorator to restrict commands to authorized user
def auth_required(func):
    async def wrapper(message, *args, **kwargs):
        authorized_id = config.get("authorized_chat_id", 0)
        if message.chat.id != authorized_id:
            await bot.reply_to(message, "❌ Unauthorized access. This bot is secured.")
            return
        return await func(message, *args, **kwargs)
    return wrapper

# Feature Calls definitions for Dynamic Greetings
def get_telegram_user(message):
    user = message.from_user
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name} {user.last_name or ''}".strip()

def get_server_status():
    hw = get_server_hardware()
    cpu = clean_hardware_name(hw.get("cpu", "Unknown"))
    gpu = clean_hardware_name(hw.get("gpu", "Unknown"))
    uptime = get_server_uptime()
    volts = get_server_voltage()
    return f"Linux Server [CPU: {cpu} | GPU: {gpu} | Uptime: {uptime} | Volt: {volts} | Status: Online]"

def get_client_status_str():
    global active_client_id
    if not active_client_id or active_client_id not in connected_clients:
        # Check if we have a known offline client MAC in config
        client_mac = config.get("client_mac", "N/A")
        client_name = config.get("client_name", "Primary Client")
        return f"{client_name} [Status: Offline | MAC: {client_mac}]"
    
    c = connected_clients[active_client_id]
    hw = c.get("hardware", {})
    cpu = clean_hardware_name(hw.get("cpu", "N/A"))
    gpu = clean_hardware_name(hw.get("gpu", "N/A"))
    uptime = c.get("uptime", "Unknown")
    volts = c.get("voltage", "N/A")
    name = c.get("name", "Client")
    return f"{name} [CPU: {cpu} | GPU: {gpu} | Uptime: {uptime} | Volt: {volts} | Status: Online]"

# Helper: Clean and shorten verbose hardware names to fit screen
def clean_hardware_name(name):
    if not name or name in ["N/A", "Offline", "Unknown"]:
        return name
    # Redundant phrase removal
    name = name.replace("Advanced Micro Devices, Inc.", "AMD")
    name = name.replace("with AMD Radeon R5 Graphics", "w/ Radeon R5")
    name = name.replace("Intel(R) Core(TM)", "Intel Core")
    name = name.replace("CPU @", "@")
    
    parts = []
    for p in name.split(","):
        p = p.strip()
        import re
        brackets = re.findall(r'\[([^\]]+)\]', p)
        if brackets:
            p = brackets[0]
        p = p.replace("Graphics Controller", "").strip()
        if len(p) > 35:
            p = p[:32] + "..."
        parts.append(p)
        
    res = ", ".join(parts)
    if len(res) > 45:
        res = res[:42] + "..."
    return res

# Neofetch ASCII Cloud Generator
def make_neofetch_greeting(user_name):
    server_status = get_server_status()
    client_status = get_client_status_str()
    
    server_hw = get_server_hardware()
    server_cpu = clean_hardware_name(server_hw.get("cpu", "Unknown"))
    
    client_cpu = "Offline"
    client_gpu = "Offline"
    if active_client_id and active_client_id in connected_clients:
        c = connected_clients[active_client_id]
        client_cpu = clean_hardware_name(c.get("hardware", {}).get("cpu", "Unknown Client CPU"))
        client_gpu = clean_hardware_name(c.get("hardware", {}).get("gpu", "Unknown Client GPU"))
        
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    time_str = datetime.datetime.now().strftime("%H:%M:%S")
    
    # Analyze weather for coordinates (active client location or server location)
    coords = server_geo
    if active_client_id and active_client_id in connected_clients:
        c = connected_clients[active_client_id]
        if c.get("coords"):
            coords = c["coords"]
            
    weather_info = fetch_weather(coords["lat"], coords["lon"])
    
    # Distance calculation
    dist_info = ""
    if active_client_id and active_client_id in connected_clients:
        c = connected_clients[active_client_id]
        if c.get("coords") and server_geo:
            d = calculate_distance(server_geo["lat"], server_geo["lon"], c["coords"]["lat"], c["coords"]["lon"])
            dist_info = f"\n🌍 Distance to Server: {d:.2f} km"

    greeting_text = (
        f"Greetings, {user_name}!,\n"
        f"Server and client status: {server_status} | {client_status},\n"
        f"The weather is: {weather_info}\n"
        f"Today is {date_str} The time is {time_str}"
        f"{dist_info}"
    )
    
    # Clean, beautiful, non-wrapping vertical layout
    neofetch = (
        "```\n"
        "      _.-'''''''-._     \n"
        "    .'  .---.      '.   \n"
        "   /   (     )       \\  \n"
        "   |  (  .---'       |  \n"
        "   \\   (________)    /  \n"
        "    '.             .'   \n"
        "      '-._______.-'     \n"
        "\n"
        "windOS Assist System Info\n"
        "-------------------------\n"
        "OS: Linux (Server) | Win/Linux (Client)\n"
        f"Server CPU: {server_cpu}\n"
        f"Client CPU: {client_cpu}\n"
        f"Client GPU: {client_gpu}\n"
        f"Server Weather: {weather_info}\n"
        "```"
    )
    
    return greeting_text + "\n\n" + neofetch

# WebSocket Server Handler
async def register(websocket, path=None):
    global active_client_id
    try:
        # Expect authorization handshake
        auth_msg = await websocket.recv()
        auth_data = json.loads(auth_msg)
        
        token = auth_data.get("token")
        if token != config.get("client_token"):
            await websocket.send(json.dumps({"status": "error", "message": "Invalid authentication token"}))
            await websocket.close()
            return
            
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        client_name = auth_data.get("name", f"Client-{client_id}")
        client_mac = auth_data.get("mac", "00:00:00:00:00:00")
        client_coords = auth_data.get("coords", None)
        client_hardware = auth_data.get("hardware", {"cpu": "Unknown", "gpu": "Unknown"})
        client_voltage = auth_data.get("voltage", "N/A")
        client_uptime = auth_data.get("uptime", "Unknown")
        
        # Save client details
        connected_clients[client_id] = {
            "websocket": websocket,
            "name": client_name,
            "mac": client_mac,
            "coords": client_coords,
            "hardware": client_hardware,
            "voltage": client_voltage,
            "uptime": client_uptime,
            "last_seen": time.time()
        }
        
        # Set client as active if it's the only one or if there was no active client
        if active_client_id is None:
            active_client_id = client_id
            
        # Update config with client MAC for WoL fallback
        config["client_mac"] = client_mac
        config["client_name"] = client_name
        save_config()
            
        logger.info(f"Client registered: {client_name} ({client_id}) with MAC {client_mac}")
        await websocket.send(json.dumps({"status": "success", "message": "Registered successfully"}))
        
        # Send notification to Telegram
        authorized_chat = config.get("authorized_chat_id", 0)
        if authorized_chat:
            await bot.send_message(authorized_chat, f"🟢 Client Connected: *{client_name}* ({client_id})", parse_mode="Markdown")
            
        # Listen for messages from client
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")
            
            # Handle screenshot response
            if msg_type == "screenshot_response":
                chat_id = data.get("chat_id")
                image_data_hex = data.get("data")
                if image_data_hex:
                    image_data = bytes.fromhex(image_data_hex)
                    with open("screenshot.png", "wb") as f:
                        f.write(image_data)
                    with open("screenshot.png", "rb") as f:
                        await bot.send_photo(chat_id, f, caption=f"📸 Screenshot from {client_name}")
                else:
                    await bot.send_message(chat_id, "❌ Failed to capture screenshot.")
            
            # Handle terminal execution response
            elif msg_type == "terminal_response":
                chat_id = data.get("chat_id")
                output = data.get("output", "")
                # Format long output
                if len(output) > 4000:
                    output = output[:4000] + "\n...[Output Truncated]..."
                if not output.strip():
                    output = "[Command executed, no output]"
                await bot.send_message(chat_id, f"```\n{output}\n```", parse_mode="Markdown")
                
            # Handle general command output (like for AI function calling)
            elif msg_type == "command_response":
                request_id = data.get("request_id")
                output = data.get("output", "")
                # Find waiting future and resolve it
                if request_id in pending_futures:
                    pending_futures[request_id].set_result(output)
                    
            # Handle status updates
            elif msg_type == "status_update":
                connected_clients[client_id]["uptime"] = data.get("uptime")
                connected_clients[client_id]["voltage"] = data.get("voltage")
                
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client connection closed: {websocket.remote_address}")
    except Exception as e:
        logger.error(f"Error handling client registration: {e}")
    finally:
        # Cleanup
        client_id_to_remove = None
        for cid, details in list(connected_clients.items()):
            if details["websocket"] == websocket:
                client_id_to_remove = cid
                client_name = details["name"]
                break
                
        if client_id_to_remove:
            connected_clients.pop(client_id_to_remove)
            logger.info(f"Client disconnected: {client_name} ({client_id_to_remove})")
            
            # Send notification to Telegram
            authorized_chat = config.get("authorized_chat_id", 0)
            if authorized_chat:
                await bot.send_message(authorized_chat, f"🔴 Client Disconnected: *{client_name}*", parse_mode="Markdown")
                
            if active_client_id == client_id_to_remove:
                if connected_clients:
                    active_client_id = list(connected_clients.keys())[0]
                    logger.info(f"Switched active client to: {connected_clients[active_client_id]['name']}")
                else:
                    active_client_id = None
                    logger.info("No active clients connected.")

# Store futures for awaiting response from clients
pending_futures = {}

async def send_command_to_client(client_id, cmd_type, payload=None):
    if client_id not in connected_clients:
        return "Error: Client is offline."
    
    ws = connected_clients[client_id]["websocket"]
    request_id = str(time.time())
    
    future = asyncio.get_event_loop().create_future()
    pending_futures[request_id] = future
    
    try:
        msg = {"type": cmd_type, "request_id": request_id, **(payload or {})}
        await ws.send(json.dumps(msg))
        # Wait for reply with 30s timeout
        result = await asyncio.wait_for(future, timeout=30.0)
        return result
    except asyncio.TimeoutError:
        return "Error: Client command timed out."
    except Exception as e:
        return f"Error sending command: {e}"
    finally:
        pending_futures.pop(request_id, None)

# AI Tool Declarations & Implementations
AI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute an arbitrary shell command (cmd.exe on Windows, bash/sh on Linux) on the active client machine and return the console stdout/stderr output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {
                        "type": "string",
                        "description": "The exact shell command line to run."
                    }
                },
                "required": ["cmd"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture a screenshot of the active client machine's screen. Saves it to disk and replies with the screenshot.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_stats",
            "description": "Retrieve exact details and statistics on CPU, RAM, Disk, voltages, hardware models, and uptimes for both server and client.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wake_client",
            "description": "Send a Wake-on-LAN packet to turn on the primary client machine from its offline state.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_client",
            "description": "Remotely power off (shut down) the active client machine.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    }
]

async def execute_ai_tool(name, arguments, chat_id):
    global active_client_id
    logger.info(f"AI requested tool execution: {name}({arguments})")
    
    if name == "run_command":
        cmd = arguments.get("cmd")
        if not active_client_id:
            return "Error: No active client connected to run terminal commands on."
        res = await send_command_to_client(active_client_id, "execute_command", {"command": cmd})
        return res
        
    elif name == "take_screenshot":
        if not active_client_id:
            return "Error: No active client connected to take screenshot from."
        # Trigger screenshot. It returns async, but we can call it and await it
        ws = connected_clients[active_client_id]["websocket"]
        await ws.send(json.dumps({"type": "capture_screenshot", "chat_id": chat_id}))
        return "Screenshot triggered. It will be sent directly to the Telegram chat shortly."
        
    elif name == "get_system_stats":
        srv_stats = get_server_status()
        cli_stats = get_client_status_str()
        return f"Server Stats: {srv_stats}\nClient Stats: {cli_stats}"
        
    elif name == "wake_client":
        mac = config.get("client_mac")
        if mac and mac != "00:00:00:00:00:00":
            sent = send_wake_on_lan(mac)
            return "Sent WoL packet to client MAC: " + mac if sent else "Failed to send WoL packet."
        return "Error: No client MAC registered in config to wake up."
        
    elif name == "shutdown_client":
        if not active_client_id:
            return "Error: Client is not online."
        res = await send_command_to_client(active_client_id, "power_action", {"action": "shutdown"})
        return res
        
    return "Error: Unknown tool."

# AI Communication Routine
async def ask_ai(prompt, chat_id):
    if not OpenAI:
        return "Mock AI Response: OpenAI package is missing. Please run `pip install openai` to enable AI features."
        
    api_key = config.get("ai_api_key")
    if not api_key:
        return "AI is not configured. Please supply an API key in the configuration or setup wizard."
        
    base_url = config.get("ai_base_url")
    model_name = config.get("ai_model")
    
    client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)
    
    # Initialize history
    if chat_id not in ai_history:
        # Set system prompt
        system_prompt = (
            "You are windOS Assist AI, a powerful, helpful assistant managing client-server networks.\n"
            "Never start every response repeating 'I am windOS Assist AI'. Only introduce yourself once at the beginning, then speak naturally.\n"
            "You have tools to interact with the client machines (run commands, take screenshots, get stats, wake/sleep machines).\n"
            "When the user asks you to perform a task, use the appropriate tools to do so, interpret the outputs, and answer directly."
        )
        ai_history[chat_id] = [{"role": "system", "content": system_prompt}]
        
    # Append user prompt
    ai_history[chat_id].append({"role": "user", "content": prompt})
    
    # Keep history manageable
    if len(ai_history[chat_id]) > 25:
        ai_history[chat_id] = [ai_history[chat_id][0]] + ai_history[chat_id][-20:]
        
    try:
        # Call API with tool declarations
        response = client.chat.completions.create(
            model=model_name,
            messages=ai_history[chat_id],
            tools=AI_TOOLS,
            tool_choice="auto"
        )
        
        response_message = response.choices[0].message
        
        # Check for tool calls
        if response_message.tool_calls:
            ai_history[chat_id].append(response_message)
            
            for tool_call in response_message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                
                # Execute tool
                tool_result = await execute_ai_tool(tool_name, tool_args, chat_id)
                
                # Send result back to model
                ai_history[chat_id].append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": tool_name,
                    "content": tool_result
                })
                
            # Call again for final answer
            second_response = client.chat.completions.create(
                model=model_name,
                messages=ai_history[chat_id]
            )
            final_content = second_response.choices[0].message.content
            ai_history[chat_id].append({"role": "assistant", "content": final_content})
            return final_content
            
        else:
            final_content = response_message.content
            ai_history[chat_id].append({"role": "assistant", "content": final_content})
            return final_content
            
    except Exception as e:
        logger.error(f"AI API failure: {e}")
        return f"AI API Error: {e}"

# Bot handlers
@bot.message_handler(commands=['start', 'help', 'greet'])
@auth_required
async def send_welcome(message):
    user_name = get_telegram_user(message)
    msg = make_neofetch_greeting(user_name)
    await bot.reply_to(message, msg, parse_mode="Markdown")

@bot.message_handler(commands=['clients'])
@auth_required
async def list_clients(message):
    global active_client_id
    if not connected_clients:
        await bot.reply_to(message, "❌ No clients connected.\nDefault Client MAC in configuration: `" + config.get("client_mac") + "`")
        return
        
    markup = types.InlineKeyboardMarkup()
    for cid, details in connected_clients.items():
        name = details["name"]
        prefix = "⭐️ " if cid == active_client_id else ""
        btn = types.InlineKeyboardButton(text=f"{prefix}{name} ({cid})", callback_data=f"select_client:{cid}")
        markup.add(btn)
        
    await bot.send_message(message.chat.id, "Select active client machine:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_client:"))
async def select_client_callback(call):
    global active_client_id
    authorized_id = config.get("authorized_chat_id", 0)
    if call.message.chat.id != authorized_id:
        return
        
    client_id = call.data.split(":", 1)[1]
    if client_id in connected_clients:
        active_client_id = client_id
        client_name = connected_clients[client_id]["name"]
        await bot.answer_callback_query(call.id, f"Switched to {client_name}")
        await bot.edit_message_text(f"⭐️ Active client set to: *{client_name}*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    else:
        await bot.answer_callback_query(call.id, "Client disconnected.")

@bot.message_handler(commands=['wakeup'])
@auth_required
async def wakeup_client(message):
    mac = config.get("client_mac")
    if mac and mac != "00:00:00:00:00:00":
        await bot.reply_to(message, f"⚡ Sending Wake-on-LAN Magic Packet to `{mac}`...")
        sent = send_wake_on_lan(mac)
        if sent:
            await bot.send_message(message.chat.id, "✅ WoL Magic Packet broadcasted successfully!")
        else:
            await bot.send_message(message.chat.id, "❌ Failed to send Magic Packet.")
    else:
        await bot.reply_to(message, "❌ No client MAC address registered. Setup a client first or configure it in settings.")

@bot.message_handler(commands=['screenshot'])
@auth_required
async def take_screenshot(message):
    global active_client_id
    if not active_client_id:
        await bot.reply_to(message, "❌ No active client connected.")
        return
        
    await bot.reply_to(message, "📸 Requesting screenshot...")
    ws = connected_clients[active_client_id]["websocket"]
    await ws.send(json.dumps({"type": "capture_screenshot", "chat_id": message.chat.id}))

@bot.message_handler(commands=['terminal', 'sh'])
@auth_required
async def enter_terminal(message):
    global active_client_id
    if not active_client_id:
        await bot.reply_to(message, "❌ No active client connected to run shell terminal on.")
        return
        
    client_name = connected_clients[active_client_id]["name"]
    terminal_sessions[message.chat.id] = active_client_id
    await bot.reply_to(message, f"🐚 *Terminal Mode Active* on *{client_name}*.\nType your shell commands. Send `exit` to close session.", parse_mode="Markdown")

@bot.message_handler(commands=['ai'])
@auth_required
async def cmd_ask_ai(message):
    prompt = message.text.split(" ", 1)
    if len(prompt) < 2:
        await bot.reply_to(message, "Usage: `/ai <your question or task>`", parse_mode="Markdown")
        return
    await bot.send_chat_action(message.chat.id, 'typing')
    response = await ask_ai(prompt[1], message.chat.id)
    await bot.reply_to(message, response)

@bot.message_handler(commands=['ai_chat'])
@auth_required
async def toggle_ai_chat(message):
    chat_id = message.chat.id
    if chat_id in ai_sessions:
        ai_sessions.pop(chat_id)
        await bot.reply_to(message, "🤖 AI continuous chat mode *Disabled*.", parse_mode="Markdown")
    else:
        ai_sessions[chat_id] = True
        await bot.reply_to(message, "🤖 AI continuous chat mode *Enabled*. I am listening...", parse_mode="Markdown")

@bot.message_handler(commands=['power'])
@auth_required
async def remote_power(message):
    global active_client_id
    if not active_client_id:
        await bot.reply_to(message, "❌ No active client connected.")
        return
        
    cmd = message.text.split(" ", 1)
    if len(cmd) < 2 or cmd[1] not in ["shutdown", "reboot", "sleep"]:
        await bot.reply_to(message, "Usage: `/power <shutdown|reboot|sleep>`", parse_mode="Markdown")
        return
        
    action = cmd[1]
    client_name = connected_clients[active_client_id]["name"]
    await bot.reply_to(message, f"⚠️ Sending *{action}* command to *{client_name}*...", parse_mode="Markdown")
    res = await send_command_to_client(active_client_id, "power_action", {"action": action})
    await bot.send_message(message.chat.id, f"Server response: {res}")

@bot.message_handler(commands=['files'])
@auth_required
async def list_files(message):
    global active_client_id
    if not active_client_id:
        await bot.reply_to(message, "❌ No active client connected.")
        return
        
    cmd = message.text.split(" ", 1)
    path = cmd[1] if len(cmd) > 1 else "."
    await bot.reply_to(message, f"📁 Browsing directory `{path}` on client...", parse_mode="Markdown")
    res = await send_command_to_client(active_client_id, "list_directory", {"path": path})
    
    # Try parsing json response
    try:
        files = json.loads(res)
        if isinstance(files, list):
            reply = f"📁 *Directory listing for* `{path}`:\n\n"
            for f in files:
                emoji = "📁" if f["isDir"] else "📄"
                size = f" ({f['size']} bytes)" if not f["isDir"] else ""
                reply += f"{emoji} `{f['name']}`{size}\n"
            await bot.send_message(message.chat.id, reply, parse_mode="Markdown")
        else:
            await bot.send_message(message.chat.id, f"Output:\n{res}")
    except Exception:
        await bot.send_message(message.chat.id, f"Output:\n{res}")

# Catch-all text messages for Terminal and AI continuous mode
@bot.message_handler(func=lambda msg: True)
@auth_required
async def handle_text(message):
    chat_id = message.chat.id
    
    # Check if inside Terminal session
    if chat_id in terminal_sessions:
        client_id = terminal_sessions[chat_id]
        if message.text.strip().lower() == "exit":
            terminal_sessions.pop(chat_id)
            await bot.reply_to(message, "🐚 Terminal session closed.")
            return
            
        if client_id not in connected_clients:
            terminal_sessions.pop(chat_id)
            await bot.reply_to(message, "❌ Terminal client disconnected. Session terminated.")
            return
            
        # Send raw command to client
        ws = connected_clients[client_id]["websocket"]
        await ws.send(json.dumps({
            "type": "execute_terminal",
            "command": message.text,
            "chat_id": chat_id
        }))
        
    # Check if inside AI continuous chat session
    elif chat_id in ai_sessions:
        await bot.send_chat_action(chat_id, 'typing')
        response = await ask_ai(message.text, chat_id)
        await bot.reply_to(message, response)
        
    else:
        # Default behavior: run as AI prompt (since user requested direct task execution in chat)
        await bot.send_chat_action(chat_id, 'typing')
        response = await ask_ai(message.text, chat_id)
        await bot.reply_to(message, response)

# Local command-line loop on Server console
def server_console_loop():
    global active_client_id
    logger.info("Local Server terminal console activated. Type 'help' for server command list.")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while True:
        try:
            raw_line = sys.stdin.readline()
            if not raw_line: # EOF reached (e.g. running under systemd)
                logger.info("Server console input closed (EOF). Exiting console loop.")
                break
            line = raw_line.strip()
            if not line:
                continue
                
            parts = line.split(" ", 1)
            cmd = parts[0].lower()
            
            if cmd == "help":
                print("Server CLI Commands:")
                print("  status         - Show current server status & list of clients")
                print("  clients        - Display connected clients details")
                print("  select <id>    - Select the active client")
                print("  wol <mac>      - Send Wake-on-LAN Magic Packet")
                print("  cmd <command>  - Run command on active client")
                print("  exit           - Shut down server")
            elif cmd == "status":
                print(get_server_status())
                print(f"Connected clients: {len(connected_clients)}")
            elif cmd == "clients":
                for cid, details in connected_clients.items():
                    act = " (ACTIVE)" if cid == active_client_id else ""
                    print(f" - {details['name']} ({cid}) MAC: {details['mac']}{act}")
            elif cmd == "select":
                if len(parts) > 1:
                    cid = parts[1]
                    if cid in connected_clients:
                        active_client_id = cid
                        print(f"Active client set to {connected_clients[cid]['name']}")
                    else:
                        print("Client ID not found.")
                else:
                    print("Usage: select <client_id>")
            elif cmd == "wol":
                if len(parts) > 1:
                    mac = parts[1]
                    send_wake_on_lan(mac)
                else:
                    print("Usage: wol <mac_address>")
            elif cmd == "cmd":
                if len(parts) > 1:
                    shell_cmd = parts[1]
                    if active_client_id:
                        # Synchronous wait for async function in CLI thread
                        fut = asyncio.run_coroutine_threadsafe(
                            send_command_to_client(active_client_id, "execute_command", {"command": shell_cmd}), 
                            loop
                        )
                        print(fut.result())
                    else:
                        print("Error: No active client connected.")
                else:
                    print("Usage: cmd <command>")
            elif cmd == "exit":
                logger.info("Shutting down server console.")
                os._exit(0)
            else:
                print(f"Unknown command: '{cmd}'. Type 'help' for list of commands.")
        except Exception as e:
            print(f"Console error: {e}")

# Server Main runner
async def main():
    # Start WebSocket Server
    port = config.get("websocket_port", 8765)
    host = config.get("websocket_host", "0.0.0.0")
    
    logger.info(f"Starting WebSocket server on ws://{host}:{port}...")
    ws_server = await websockets.serve(register, host, port)
    
    # Start Telegram Bot polling
    logger.info("Starting Telegram Bot listener...")
    bot_task = asyncio.create_task(bot.polling(non_stop=True))
    
    # Keep running
    await asyncio.gather(
        ws_server.wait_closed(),
        bot_task
    )

if __name__ == "__main__":
    if not config.get("telegram_token"):
        logger.error("Telegram bot token not found in server_config.json. Run setup.py first!")
        sys.exit(1)
        
    # Start server console CLI thread
    t = Thread(target=server_console_loop, daemon=True)
    t.start()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")
