import asyncio
import json
import os
import sys
import uuid
import socket
import time
import subprocess
import datetime
import urllib.request
import logging
from threading import Thread

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("windOS-Client")

# Dependencies
try:
    import websockets
except ImportError:
    logger.error("Required package 'websockets' not found. Please install via: pip install websockets")
    sys.exit(1)

try:
    from PIL import ImageGrab
except ImportError:
    logger.warning("Package 'pillow' not found. Screenshots will fail until installed: pip install pillow")
    ImageGrab = None

try:
    import psutil
except ImportError:
    psutil = None
    logger.warning("Package 'psutil' not found. System stats will be limited until installed: pip install psutil")

try:
    import telebot
    from telebot.async_telebot import AsyncTeleBot
except ImportError:
    telebot = None
    AsyncTeleBot = None
    logger.warning("Package 'pyTelegramBotAPI' not found. Fallback Bot will be disabled until installed: pip install pyTelegramBotAPI")

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_config.json")

# State
config = {}
connected_to_server = False
fallback_bot_running = False
fallback_bot = None
persistent_shell = None

def load_config():
    global config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                config = json.load(f)
            logger.info("Configuration loaded.")
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
    else:
        logger.warning("Configuration file not found. Running with defaults.")
        config = {
            "server_url": "ws://localhost:8765",
            "client_token": "windos_secret_token",
            "name": socket.gethostname(),
            "server_mac": "00:00:00:00:00:00",
            "telegram_token": "",
            "authorized_chat_id": 0,
            "ai_provider": "google",
            "ai_api_key": "",
            "ai_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "ai_model": "gemini-1.5-flash"
        }

load_config()

# Helper: Get local MAC address
def get_mac_address():
    try:
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1])
        return mac
    except Exception:
        return "00:00:00:00:00:00"

# Helper: Get Hardware (CPU & GPU) Details
def get_hardware_info():
    cpu = "Unknown CPU"
    gpu = "Unknown GPU"
    
    # Windows Hardware detection
    if sys.platform.startswith("win"):
        try:
            # Query CPU via wmic
            cpu_raw = subprocess.check_output("wmic cpu get name", shell=True).decode().strip()
            cpu_lines = [l.strip() for l in cpu_raw.split("\n") if l.strip()]
            if len(cpu_lines) > 1:
                cpu = cpu_lines[1]
        except Exception:
            try:
                import platform
                cpu = platform.processor()
            except Exception:
                pass
                
        try:
            # Query GPU via wmic
            gpu_raw = subprocess.check_output("wmic path win32_VideoController get name", shell=True).decode().strip()
            gpu_lines = [l.strip() for l in gpu_raw.split("\n") if l.strip()]
            if len(gpu_lines) > 1:
                gpu = ", ".join(gpu_lines[1:])
        except Exception:
            pass
            
    # Linux Hardware detection
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        cpu = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
            
        try:
            gpu_raw = subprocess.check_output("lspci | grep -i -E 'vga|3d|display'", shell=True).decode().strip()
            gpu_lines = []
            for line in gpu_raw.split("\n"):
                if ":" in line:
                    gpu_lines.append(line.split(":", 2)[-1].strip())
            if gpu_lines:
                gpu = ", ".join(gpu_lines)
        except Exception:
            pass
            
    return {"cpu": cpu, "gpu": gpu}

# Helper: Get Voltage
def get_voltage():
    # Windows
    if sys.platform.startswith("win"):
        try:
            # Try WMI design voltage for battery if laptop
            val = subprocess.check_output("wmic path Win32_Battery get DesignVoltage", shell=True).decode().strip()
            lines = [l.strip() for l in val.split("\n") if l.strip()]
            if len(lines) > 1:
                volts = float(lines[1]) / 1000.0
                return f"{volts:.2f}V (Battery)"
        except Exception:
            pass
            
        try:
            # Try processor voltage
            val = subprocess.check_output("wmic path Win32_Processor get CurrentVoltage", shell=True).decode().strip()
            lines = [l.strip() for l in val.split("\n") if l.strip()]
            if len(lines) > 1:
                # WMI returns voltage * 10
                volts = float(lines[1]) / 10.0
                if volts > 0:
                    return f"{volts:.2f}V"
        except Exception:
            pass
            
    # Linux
    elif sys.platform.startswith("linux"):
        try:
            for i in range(10):
                path = f"/sys/class/hwmon/hwmon{i}/in0_input"
                if os.path.exists(path):
                    with open(path, "r") as f:
                        volts = float(f.read().strip()) / 1000.0
                        return f"{volts:.2f}V"
        except Exception:
            pass
        try:
            path = "/sys/class/power_supply/BAT0/voltage_now"
            if os.path.exists(path):
                with open(path, "r") as f:
                    volts = float(f.read().strip()) / 1000000.0
                    return f"{volts:.2f}V (Battery)"
        except Exception:
            pass
            
    return "N/A"

# Helper: Get Uptime
def get_system_uptime():
    if psutil:
        try:
            boot_time = psutil.boot_time()
            uptime = time.time() - boot_time
            return str(datetime.timedelta(seconds=int(uptime)))
        except Exception:
            pass
            
    if sys.platform.startswith("win"):
        try:
            val = subprocess.check_output("wmic os get lastbootuptime", shell=True).decode().strip()
            lines = [l.strip() for l in val.split("\n") if l.strip()]
            if len(lines) > 1:
                # Format: YYYYMMDDHHMMSS.MMMMMM+ZZZ
                boot_str = lines[1].split(".")[0]
                boot_dt = datetime.datetime.strptime(boot_str, "%Y%m%d%H%M%S")
                uptime = datetime.datetime.now() - boot_dt
                return str(uptime).split(".")[0]
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
                return str(datetime.timedelta(seconds=int(uptime_seconds)))
        except Exception:
            pass
            
    return "Unknown Uptime"

# Geolocation lookup via IP-API
def get_ip_geolocation():
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
        logger.warning(f"IP geolocation check failed: {e}")
    return None

# Wake on LAN sender (used to boot server)
def send_wake_on_lan(mac_address):
    try:
        if len(mac_address) == 17:
            sep = mac_address[2]
            mac_address = mac_address.replace(sep, "")
        elif len(mac_address) != 12:
            raise ValueError("Invalid MAC address")
            
        data = bytes.fromhex("F" * 12 + mac_address * 16)
        soc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        soc.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        soc.sendto(data, ('255.255.255.255', 9))
        soc.close()
        logger.info(f"WoL Magic Packet sent to Server MAC: {mac_address}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Wake-on-LAN: {e}")
        return False

# Persistent Shell Subprocess Controller
class InteractiveShell:
    def __init__(self):
        # Start appropriate shell
        shell = "cmd.exe" if sys.platform.startswith("win") else "/bin/sh"
        self.proc = subprocess.Popen(
            shell,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            text=True,
            bufsize=1
        )
        self.delimiter = "--WINDOS_CMD_DONE--"
        
    def execute(self, cmd):
        if self.proc.poll() is not None:
            # Restart if dead
            self.__init__()
            
        # Format command with delimiter echo
        if sys.platform.startswith("win"):
            full_cmd = f"{cmd}\necho {self.delimiter}\n"
        else:
            full_cmd = f"{cmd}\necho {self.delimiter}\n"
            
        try:
            self.proc.stdin.write(full_cmd)
            self.proc.stdin.flush()
        except Exception as e:
            return f"Error writing to shell: {e}"
            
        # Read lines until delimiter matches
        output = []
        timeout = time.time() + 15.0 # 15s timeout
        
        while time.time() < timeout:
            # Make reading non-blocking by checking with select or waiting a tiny bit
            # Python's readline can block. To avoid blocking indefinitely:
            # On Windows, we can use a quick read thread or read line-by-line
            line = self.proc.stdout.readline()
            if not line:
                break
            if self.delimiter in line:
                break
            output.append(line)
            
        return "".join(output)

# Initialize Shell session
def get_persistent_shell():
    global persistent_shell
    if not persistent_shell:
        persistent_shell = InteractiveShell()
    return persistent_shell

# System Power Actions
def execute_power_action(action):
    try:
        if action == "shutdown":
            if sys.platform.startswith("win"):
                subprocess.Popen("shutdown /s /t 0", shell=True)
            else:
                subprocess.Popen("systemctl poweroff", shell=True)
            return "Shutdown command dispatched."
            
        elif action == "reboot":
            if sys.platform.startswith("win"):
                subprocess.Popen("shutdown /r /t 0", shell=True)
            else:
                subprocess.Popen("systemctl reboot", shell=True)
            return "Reboot command dispatched."
            
        elif action == "sleep":
            if sys.platform.startswith("win"):
                # Rundll32 power command
                subprocess.Popen("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
            else:
                subprocess.Popen("systemctl suspend", shell=True)
            return "Sleep command dispatched."
    except Exception as e:
        return f"Power action failed: {e}"
    return "Unknown action."

# Take screen capture
def take_screenshot():
    if not ImageGrab:
        return None
        
    try:
        # Capture full display
        img = ImageGrab.grab()
        path = "temp_screen.png"
        img.save(path)
        with open(path, "rb") as f:
            data = f.read()
        os.remove(path)
        return data.hex()
    except Exception as e:
        logger.error(f"Error capturing screenshot: {e}")
        return None

# Directory listing
def get_directory_listing(path):
    try:
        if path == ".":
            path = os.getcwd()
        if not os.path.exists(path):
            return json.dumps({"status": "error", "message": "Path not found."})
            
        items = []
        for entry in os.scandir(path):
            try:
                items.append({
                    "name": entry.name,
                    "isDir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0
                })
            except Exception:
                pass
        return json.dumps(items)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

# WebSocket messaging loop
async def websocket_loop():
    global connected_to_server
    server_url = config.get("server_url", "ws://localhost:8765")
    token = config.get("client_token", "")
    
    logger.info(f"Connecting to windOS Server at {server_url}...")
    
    async with websockets.connect(server_url) as ws:
        connected_to_server = True
        logger.info("Connected to server! Sending handshake...")
        
        # Get hardware and network stats
        mac = get_mac_address()
        coords = get_ip_geolocation()
        hw = get_hardware_info()
        volts = get_voltage()
        uptime = get_system_uptime()
        
        handshake = {
            "token": token,
            "name": config.get("name", socket.gethostname()),
            "mac": mac,
            "coords": coords,
            "hardware": hw,
            "voltage": volts,
            "uptime": uptime
        }
        
        await ws.send(json.dumps(handshake))
        resp = await ws.recv()
        resp_data = json.loads(resp)
        if resp_data.get("status") != "success":
            logger.error(f"Handshake failed: {resp_data.get('message')}")
            await ws.close()
            connected_to_server = False
            return
            
        logger.info("Handshake verified successfully!")
        
        # Start periodic status updates
        async def status_updater():
            while connected_to_server:
                try:
                    await asyncio.sleep(30.0)
                    up = {
                        "type": "status_update",
                        "voltage": get_voltage(),
                        "uptime": get_system_uptime()
                    }
                    await ws.send(json.dumps(up))
                except Exception:
                    break
                    
        updater_task = asyncio.create_task(status_updater())
        
        # Process server messages
        async for message in ws:
            data = json.loads(message)
            msg_type = data.get("type")
            req_id = data.get("request_id")
            
            if msg_type == "capture_screenshot":
                chat_id = data.get("chat_id")
                hex_data = take_screenshot()
                reply = {
                    "type": "screenshot_response",
                    "chat_id": chat_id,
                    "data": hex_data
                }
                await ws.send(json.dumps(reply))
                
            elif msg_type == "execute_terminal":
                chat_id = data.get("chat_id")
                cmd = data.get("command")
                sh = get_persistent_shell()
                output = sh.execute(cmd)
                reply = {
                    "type": "terminal_response",
                    "chat_id": chat_id,
                    "output": output
                }
                await ws.send(json.dumps(reply))
                
            elif msg_type == "execute_command":
                cmd = data.get("command")
                # One-off command execution (e.g. for AI function calling)
                try:
                    res = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode()
                except subprocess.CalledProcessError as e:
                    res = e.output.decode()
                except Exception as e:
                    res = str(e)
                reply = {
                    "type": "command_response",
                    "request_id": req_id,
                    "output": res
                }
                await ws.send(json.dumps(reply))
                
            elif msg_type == "power_action":
                action = data.get("action")
                res = execute_power_action(action)
                reply = {
                    "type": "command_response",
                    "request_id": req_id,
                    "output": res
                }
                await ws.send(json.dumps(reply))
                
            elif msg_type == "list_directory":
                path = data.get("path", ".")
                res = get_directory_listing(path)
                reply = {
                    "type": "command_response",
                    "request_id": req_id,
                    "output": res
                }
                await ws.send(json.dumps(reply))
                
        connected_to_server = False
        updater_task.cancel()

# Server death prompt (blocking console check or non-blocking prompt)
def check_server_death_reconnect():
    global connected_to_server
    if connected_to_server:
        return
        
    print("\n" + "="*80)
    print("Warning: Your server either crashed, powered itself off, or just was tired from it's misery,")
    print("want to send a magical packet to return it to work? (y/n)")
    print("="*80)
    
    # Quick non-blocking read with timeout or simple input
    # Since it is in a thread, standard input is fine
    try:
        ans = sys.stdin.readline().strip().lower()
        if ans == 'y':
            mac = config.get("server_mac")
            if mac and mac != "00:00:00:00:00:00":
                send_wake_on_lan(mac)
            else:
                print("No server MAC address configured in client_config.json.")
    except Exception:
        pass

# Fallback Telegram Bot Setup (Runs directly on Client if Server is offline)
async def start_fallback_bot():
    global fallback_bot_running, fallback_bot
    if not telebot or not config.get("telegram_token"):
        logger.warning("Fallback bot cannot start: Token is missing or pyTelegramBotAPI package is not installed.")
        return
        
    fallback_bot_running = True
    logger.info("⚡ Initializing Fallback Bot locally on Client...")
    
    fallback_bot = AsyncTeleBot(config.get("telegram_token"))
    
    # Decorator to restrict to authorized user
    def client_auth_required(func):
        async def wrapper(message, *args, **kwargs):
            if message.chat.id != config.get("authorized_chat_id", 0):
                await fallback_bot.reply_to(message, "❌ Unauthorized client bot access.")
                return
            return await func(message, *args, **kwargs)
        return wrapper
        
    @fallback_bot.message_handler(commands=['start', 'help'])
    @client_auth_required
    async def fb_help(message):
        name = config.get("name", socket.gethostname())
        await fallback_bot.reply_to(
            message,
            f"⚠️ *FALLBACK MODE ACTIVE* ⚠️\n"
            f"Currently running directly on client machine *{name}*.\n"
            f"Commands available:\n"
            f" - /sysinfo: System stats\n"
            f" - /screenshot: Capture desktop\n"
            f" - /cmd <command>: Run command\n"
            f" - /power <shutdown|reboot|sleep>: Power control",
            parse_mode="Markdown"
        )
        
    @fallback_bot.message_handler(commands=['sysinfo'])
    @client_auth_required
    async def fb_sysinfo(message):
        hw = get_hardware_info()
        uptime = get_system_uptime()
        volts = get_voltage()
        await fallback_bot.reply_to(
            message,
            f"🖥️ *Client Fallback Info*:\n"
            f"CPU: `{hw['cpu']}`\n"
            f"GPU: `{hw['gpu']}`\n"
            f"Uptime: `{uptime}`\n"
            f"Voltage: `{volts}`",
            parse_mode="Markdown"
        )
        
    @fallback_bot.message_handler(commands=['screenshot'])
    @client_auth_required
    async def fb_screenshot(message):
        await fallback_bot.reply_to(message, "📸 Capturing screenshot...")
        hex_data = take_screenshot()
        if hex_data:
            img_bytes = bytes.fromhex(hex_data)
            with open("fallback_screen.png", "wb") as f:
                f.write(img_bytes)
            with open("fallback_screen.png", "rb") as f:
                await fallback_bot.send_photo(message.chat.id, f)
            os.remove("fallback_screen.png")
        else:
            await fallback_bot.send_message(message.chat.id, "❌ Screenshot failed.")
            
    @fallback_bot.message_handler(commands=['cmd'])
    @client_auth_required
    async def fb_cmd(message):
        cmd = message.text.split(" ", 1)
        if len(cmd) < 2:
            await fallback_bot.reply_to(message, "Usage: `/cmd <command>`", parse_mode="Markdown")
            return
        shell_cmd = cmd[1]
        try:
            res = subprocess.check_output(shell_cmd, shell=True, stderr=subprocess.STDOUT).decode()
        except subprocess.CalledProcessError as e:
            res = e.output.decode()
        except Exception as e:
            res = str(e)
            
        if len(res) > 4000:
            res = res[:4000] + "\n...[Truncated]..."
        await fallback_bot.reply_to(message, f"```\n{res}\n```", parse_mode="Markdown")
        
    @fallback_bot.message_handler(commands=['power'])
    @client_auth_required
    async def fb_power(message):
        cmd = message.text.split(" ", 1)
        if len(cmd) < 2 or cmd[1] not in ["shutdown", "reboot", "sleep"]:
            await fallback_bot.reply_to(message, "Usage: `/power <shutdown|reboot|sleep>`", parse_mode="Markdown")
            return
        action = cmd[1]
        await fallback_bot.reply_to(message, f"Dispatched power action: {action}")
        execute_power_action(action)

    # Start Polling
    try:
        await fallback_bot.polling(non_stop=True)
    except Exception as e:
        logger.error(f"Fallback bot polling error: {e}")
        fallback_bot_running = False

async def main():
    global connected_to_server, fallback_bot_running, fallback_bot
    backoff = 2.0
    
    # Perform startup server status check & WoL warning trigger in a separate thread
    Thread(target=check_server_death_reconnect, daemon=True).start()
    await asyncio.sleep(2.0) # Wait a moment for the user to answer the WoL prompt
    
    while True:
        try:
            # 1. Try connecting to server
            await websocket_loop()
            backoff = 2.0 # Reset backoff on successful run
        except Exception as e:
            logger.warning(f"Connection to server failed: {e}")
            connected_to_server = False
            
            # 2. Trigger fallback bot if connection is dead
            if not fallback_bot_running and telebot and config.get("telegram_token"):
                asyncio.create_task(start_fallback_bot())
                
            # 3. Connection retry with backoff
            logger.info(f"Retrying connection to server in {backoff} seconds...")
            
            # While waiting, we check if the fallback bot should be paused to test the connection
            # If the fallback bot is running, we stop polling before trying the websocket, to avoid bot collision
            await asyncio.sleep(backoff)
            
            # Pause fallback bot polling before retry to prevent conflict
            if fallback_bot_running and fallback_bot:
                logger.info("Pausing fallback bot polling to test server connection...")
                try:
                    await fallback_bot.close_session()
                    fallback_bot_running = False
                except Exception:
                    pass
            
            backoff = min(backoff * 1.5, 60.0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Client terminated.")
