import os
import sys
import json
import socket
import subprocess
import urllib.request
import secrets

def load_env_api_key():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("GOOGLE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return ""

GOOGLE_KEY = load_env_api_key()

# ANSI escape codes for beautiful colorized terminal output
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_header(title):
    print(f"\n{BOLD}{CYAN}" + "="*60)
    print(f"  {title.center(56)}")
    print("="*60 + f"{RESET}\n")

def print_success(msg):
    print(f"{BOLD}{GREEN}✓ {msg}{RESET}")

def print_info(msg):
    print(f"{CYAN}i {msg}{RESET}")

def print_warning(msg):
    print(f"{BOLD}{YELLOW}⚠ {msg}{RESET}")

def print_error(msg):
    print(f"{BOLD}{RED}✗ {msg}{RESET}")

# Ensure SSH paramiko is installed
try:
    import paramiko
except ImportError:
    print_info("Installing required dependency 'paramiko' for remote SSH operations...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
        import paramiko
        print_success("Paramiko installed successfully!")
    except Exception as e:
        print_error(f"Failed to install paramiko: {e}. Remote setup will be disabled.")
        paramiko = None

# Default github repos for downloading files
DEFAULT_REPO = "winmasttdev/windOS-Assist"
DEFAULT_BRANCH = "main"

def fetch_file_from_github(repo, branch, filepath, destination):
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{filepath}"
    print_info(f"Downloading {filepath} from GitHub...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            content = response.read()
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "wb") as f:
            f.write(content)
        print_success(f"Successfully downloaded to {destination}")
        return True
    except Exception as e:
        print_error(f"Failed to download {filepath}: {e}")
        return False

# GitHub Downloader Step
def check_and_fetch_files():
    print_header("GitHub Code Synchronization")
    ans = input(f"{BOLD}Would you like to fetch client and server code from GitHub? (y/n) [n]: {RESET}").strip().lower()
    if ans == 'y':
        repo = input(f"Enter GitHub repository [default: {DEFAULT_REPO}]: ").strip() or DEFAULT_REPO
        branch = input(f"Enter branch [default: {DEFAULT_BRANCH}]: ").strip() or DEFAULT_BRANCH
        
        server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "server.py")
        client_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client", "client.py")
        
        s_ok = fetch_file_from_github(repo, branch, "server/server.py", server_path)
        c_ok = fetch_file_from_github(repo, branch, "client/client.py", client_path)
        
        if s_ok and c_ok:
            print_success("All files fetched successfully from GitHub!")
        else:
            print_warning("Some files failed to download. Setup will fall back to local directory files.")
    else:
        print_info("Skipping GitHub sync, using local files.")

# Install local dependencies
def install_local_dependencies(is_server=False):
    print_header("Installing Dependencies")
    deps = ["websockets"]
    if is_server:
        deps.extend(["pyTelegramBotAPI", "openai"])
    else:
        deps.extend(["pillow", "psutil", "pyTelegramBotAPI"])
        
    print_info(f"Installing: {', '.join(deps)}")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install"] + deps)
        print_success("Dependencies installed successfully!")
    except Exception as e:
        print_error(f"Error installing dependencies: {e}")

# SSH Remote Server Setup
def remote_server_setup(server_ip, server_port, username, password, key_path, token):
    if not paramiko:
        print_error("Paramiko is not available. Aborting remote setup.")
        return None
        
    print_info(f"Connecting to Linux Server at {server_ip}:{server_port} via SSH...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        if key_path:
            ssh.connect(server_ip, port=server_port, username=username, key_filename=key_path, timeout=10)
        else:
            ssh.connect(server_ip, port=server_port, username=username, password=password, timeout=10)
            
        print_success("Connected to remote server over SSH!")
        
        # Read the local server script path
        server_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "server.py")
        if not os.path.exists(server_local_path):
            print_error(f"Local server file not found at {server_local_path}. Cannot upload.")
            return None
            
        # Get server MAC address dynamically over SSH
        print_info("Detecting server MAC address...")
        mac_cmd = "cat /sys/class/net/$(ip route show | grep default | awk '{print $5}')/address"
        stdin, stdout, stderr = ssh.exec_command(mac_cmd)
        server_mac = stdout.read().decode().strip().upper()
        if not server_mac or len(server_mac) != 17:
            # Fallback MAC lookup
            stdin, stdout, stderr = ssh.exec_command("ip link show | grep -o 'ether [0-9a-f:]*' | head -n 1 | awk '{print $2}'")
            server_mac = stdout.read().decode().strip().upper()
            
        print_success(f"Detected Server MAC address: {server_mac}")
        
        # Create server directories on Linux
        ssh.exec_command("sudo mkdir -p /opt/windos-assist/server")
        ssh.exec_command(f"sudo chown -R {username}:{username} /opt/windos-assist")
        
        # Upload server.py using SFTP
        print_info("Uploading server.py to remote server...")
        sftp = ssh.open_sftp()
        sftp.put(server_local_path, "/opt/windos-assist/server/server.py")
        sftp.close()
        print_success("server.py uploaded successfully!")
        
        # Prompt for Telegram token & chat_id
        print_header("Telegram Bot Settings")
        telegram_token = input(f"{BOLD}Enter Telegram Bot Token: {RESET}").strip()
        auth_chat = int(input(f"{BOLD}Enter Authorized Chat ID (integer): {RESET}").strip())
        
        # Prompt for AI settings
        print_header("AI Assistant Configuration")
        ai_prov = input(f"{BOLD}Enter AI Provider (google/openai/ollama/custom) [google]: {RESET}").strip().lower() or "google"
        ai_key = ""
        ai_base = ""
        ai_model = ""
        
        if ai_prov in ["google", "openai", "custom"]:
            default_key = GOOGLE_KEY if ai_prov == "google" else ""
            prompt_str = f"Enter API Key [default: {default_key}]: " if default_key else "Enter API Key: "
            ai_key = input(f"{BOLD}{prompt_str}{RESET}").strip() or default_key
            
        if ai_prov == "google":
            ai_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
            ai_model = "gemini-3.1-flash-lite"
        elif ai_prov == "openai":
            ai_base = "https://api.openai.com/v1"
            ai_model = "gpt-4o-mini"
        elif ai_prov == "ollama":
            ai_base = "http://localhost:11434/v1"
            ai_model = "llama3"
        else:
            ai_base = input(f"{BOLD}Enter Base URL (e.g. OpenRouter URL): {RESET}").strip()
            ai_model = input(f"{BOLD}Enter Model Name: {RESET}").strip()
            
        # Remote config dictionary
        srv_config = {
            "telegram_token": telegram_token,
            "authorized_chat_id": auth_chat,
            "websocket_port": 8765,
            "websocket_host": "0.0.0.0",
            "client_token": token,
            "ai_provider": ai_prov,
            "ai_api_key": ai_key,
            "ai_base_url": ai_base,
            "ai_model": ai_model,
            "server_mac": server_mac
        }
        
        # Write config remotely via SSH
        print_info("Configuring remote server settings...")
        config_json = json.dumps(srv_config, indent=4)
        # Write file remotely using cat
        ssh.exec_command(f"cat << 'EOF' > /opt/windos-assist/server/server_config.json\n{config_json}\nEOF")
        
        # Setup Python Virtual Environment and install server dependencies remotely
        print_info("Installing python packages on remote server...")
        ssh.exec_command("sudo apt-get update && sudo apt-get install -y python3-pip python3-venv")
        # Run pip install
        stdin, stdout, stderr = ssh.exec_command("pip3 install websockets pyTelegramBotAPI openai")
        stdout.channel.recv_exit_status() # Wait for completion
        
        # Setup systemd service remotely
        print_info("Creating remote systemd service...")
        service_file = (
            "[Unit]\n"
            "Description=windOS Assist Server Daemon\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            "WorkingDirectory=/opt/windos-assist/server\n"
            "ExecStart=/usr/bin/python3 /opt/windos-assist/server/server.py\n"
            "Restart=always\n"
            "RestartSec=5\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        ssh.exec_command(f"cat << 'EOF' > /tmp/windos-assist-server.service\n{service_file}\nEOF")
        ssh.exec_command("sudo mv /tmp/windos-assist-server.service /etc/systemd/system/windos-assist-server.service")
        
        # Reload daemon and start server
        print_info("Enabling and starting service...")
        ssh.exec_command("sudo systemctl daemon-reload")
        ssh.exec_command("sudo systemctl enable windos-assist-server.service")
        ssh.exec_command("sudo systemctl start windos-assist-server.service")
        
        print_success("Remote server setup complete and started running!")
        ssh.close()
        
        return {
            "server_mac": server_mac,
            "telegram_token": telegram_token,
            "authorized_chat_id": auth_chat,
            "ai_provider": ai_prov,
            "ai_api_key": ai_key,
            "ai_base_url": ai_base,
            "ai_model": ai_model
        }
        
    except Exception as e:
        print_error(f"Error during remote setup: {e}")
        try:
            ssh.close()
        except Exception:
            pass
        return None

# Setup local client on Windows (This PC)
def local_client_setup(server_ip, token, server_mac, tg_token, chat_id, ai_prov, ai_key, ai_base, ai_model):
    print_header("Configuring Local Client")
    client_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client")
    os.makedirs(client_dir, exist_ok=True)
    
    # Client configuration json
    client_config = {
        "server_url": f"ws://{server_ip}:8765",
        "client_token": token,
        "name": socket.gethostname(),
        "server_mac": server_mac,
        "telegram_token": tg_token,
        "authorized_chat_id": chat_id,
        "ai_provider": ai_prov,
        "ai_api_key": ai_key,
        "ai_base_url": ai_base,
        "ai_model": ai_model
    }
    
    config_path = os.path.join(client_dir, "client_config.json")
    with open(config_path, "w") as f:
        json.dump(client_config, f, indent=4)
        
    print_success(f"Client configuration saved to {config_path}")
    
    # Dependencies install
    install_local_dependencies(is_server=False)
    
    # Startup persistence setup (Windows Task Scheduler hidden via VBS)
    print_info("Setting up startup persistence for Windows...")
    
    # 1. Write the hidden run_client.vbs launcher
    vbs_path = os.path.join(client_dir, "run_client.vbs")
    client_script_path = os.path.join(client_dir, "client.py")
    
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "python ""{client_script_path}""", 0, False\n'
    )
    with open(vbs_path, "w") as f:
        f.write(vbs_content)
    print_success("Created run_client.vbs launcher.")
    
    # 2. Register Windows Task Scheduler task using schtasks
    try:
        # Create Task running VBS at User logon
        task_name = "windOS_Assist_Client"
        cmd = f'schtasks /create /tn "{task_name}" /tr "wscript.exe \\"{vbs_path}\\"" /sc onlogon /f'
        subprocess.check_call(cmd, shell=True)
        print_success("Registered Windows Task Scheduler task to run silently at logon!")
    except Exception as e:
        print_warning(f"Failed to register Windows Task: {e}. You can start the client manually.")
        
    # Start the client right now
    print_info("Launching the local client in background...")
    try:
        subprocess.Popen(f'wscript.exe "{vbs_path}"', shell=True)
        print_success("Client launched!")
    except Exception as e:
        print_error(f"Failed to launch client: {e}")

# Local Server Only Setup (Linux)
def local_server_setup_standalone():
    print_header("Standalone Server Setup (Local Machine)")
    if not sys.platform.startswith("linux"):
        print_error("Server is designed for Linux only. This machine is running Windows.")
        return
        
    token = secrets.token_hex(16)
    server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server")
    os.makedirs(server_dir, exist_ok=True)
    
    telegram_token = input(f"{BOLD}Enter Telegram Bot Token: {RESET}").strip()
    auth_chat = int(input(f"{BOLD}Enter Authorized Chat ID (integer): {RESET}").strip())
    
    # AI settings
    ai_prov = input(f"{BOLD}Enter AI Provider (google/openai/ollama/custom) [google]: {RESET}").strip().lower() or "google"
    ai_key = ""
    ai_base = ""
    ai_model = ""
    
    if ai_prov in ["google", "openai", "custom"]:
        default_key = GOOGLE_KEY if ai_prov == "google" else ""
        prompt_str = f"Enter API Key [default: {default_key}]: " if default_key else "Enter API Key: "
        ai_key = input(f"{BOLD}{prompt_str}{RESET}").strip() or default_key
        
    if ai_prov == "google":
        ai_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
        ai_model = "gemini-3.1-flash-lite"
    elif ai_prov == "openai":
        ai_base = "https://api.openai.com/v1"
        ai_model = "gpt-4o-mini"
    elif ai_prov == "ollama":
        ai_base = "http://localhost:11434/v1"
        ai_model = "llama3"
    else:
        ai_base = input(f"{BOLD}Enter Base URL: {RESET}").strip()
        ai_model = input(f"{BOLD}Enter Model Name: {RESET}").strip()
        
    # Detect local server MAC
    try:
        import uuid
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1]).upper()
    except Exception:
        mac = "00:00:00:00:00:00"
        
    srv_config = {
        "telegram_token": telegram_token,
        "authorized_chat_id": auth_chat,
        "websocket_port": 8765,
        "websocket_host": "0.0.0.0",
        "client_token": token,
        "ai_provider": ai_prov,
        "ai_api_key": ai_key,
        "ai_base_url": ai_base,
        "ai_model": ai_model,
        "server_mac": mac
    }
    
    config_path = os.path.join(server_dir, "server_config.json")
    with open(config_path, "w") as f:
        json.dump(srv_config, f, indent=4)
        
    print_success(f"Configuration saved to {config_path}")
    install_local_dependencies(is_server=True)
    
    # Print out instructions
    print_info("To run the server as a systemd service, copy and run these commands:")
    print(f"sudo ln -sf {os.path.join(server_dir, 'server.py')} /opt/windos-assist-server.py")
    print("Wait... Or follow the systemd instructions in the README.")

def main_wizard():
    print_header("windOS Assist Setup Wizard")
    print_info("Welcome! Let's get windOS Assist configured.")
    
    # 1. Check and fetch code files from Github if requested
    check_and_fetch_files()
    
    print_header("Select Setup Mode")
    print(" 1) Full Setup (Remotely configure Linux Server + Configure local Windows client) - RECOMMENDED")
    print(" 2) Local Client Only (Configure local machine as client, connect to existing server)")
    print(" 3) Local Server Only (Configure local machine as Linux server - Linux Only)")
    print("="*60)
    
    choice = input(f"{BOLD}Enter your choice [1-3]: {RESET}").strip()
    
    if choice == '1':
        print_header("Configure Remote Linux Server SSH Connection")
        server_ip = input(f"{BOLD}Enter Linux Server IP / Hostname: {RESET}").strip()
        server_port = int(input(f"{BOLD}Enter SSH Port [22]: {RESET}").strip() or "22")
        username = input(f"{BOLD}Enter SSH Username (e.g. root, pi, ubuntu): {RESET}").strip()
        
        auth_mode = input(f"{BOLD}Use Password (p) or SSH Private Key (k) [p]: {RESET}").strip().lower() or "p"
        password = None
        key_path = None
        
        if auth_mode == 'k':
            key_path = input(f"{BOLD}Enter path to private key file: {RESET}").strip()
        else:
            password = input(f"{BOLD}Enter SSH Password: {RESET}").strip()
            
        # Generate secure token
        token = secrets.token_hex(16)
        
        # Deploy Server
        server_details = remote_server_setup(server_ip, server_port, username, password, key_path, token)
        
        if server_details:
            # Setup local client
            local_client_setup(
                server_ip=server_ip,
                token=token,
                server_mac=server_details["server_mac"],
                tg_token=server_details["telegram_token"],
                chat_id=server_details["authorized_chat_id"],
                ai_prov=server_details["ai_provider"],
                ai_key=server_details["ai_api_key"],
                ai_base=server_details["ai_base_url"],
                ai_model=server_details["ai_model"]
            )
            print_header("windOS Assist Setup Complete!")
            print_success("Server and Client configured and running in background!")
            print_info("You can now go to Telegram and start chat with your bot using '/start'.")
        else:
            print_error("Server setup failed. Cannot configure local client.")
            
    elif choice == '2':
        print_header("Configure Client Mode")
        server_ip = input(f"{BOLD}Enter Server IP Address: {RESET}").strip()
        token = input(f"{BOLD}Enter Client Connection Token: {RESET}").strip()
        server_mac = input(f"{BOLD}Enter Server MAC Address (for WoL support) [00:00:00:00:00:00]: {RESET}").strip() or "00:00:00:00:00:00"
        
        # Shared Fallback credentials
        tg_token = input(f"{BOLD}Enter Telegram Bot Token (for Fallback bot, optional): {RESET}").strip()
        chat_id = 0
        if tg_token:
            chat_id = int(input(f"{BOLD}Enter Authorized Telegram Chat ID (integer): {RESET}").strip() or "0")
            
        ai_prov = "google"
        ai_key = ""
        ai_base = ""
        ai_model = ""
        
        if tg_token:
            print_header("Fallback AI Settings")
            ai_prov = input(f"{BOLD}Enter AI Provider (google/openai/ollama/custom) [google]: {RESET}").strip().lower() or "google"
            if ai_prov in ["google", "openai", "custom"]:
                default_key = GOOGLE_KEY if ai_prov == "google" else ""
                prompt_str = f"Enter API Key [default: {default_key}]: " if default_key else "Enter API Key: "
                ai_key = input(f"{BOLD}{prompt_str}{RESET}").strip() or default_key
            if ai_prov == "google":
                ai_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
                ai_model = "gemini-3.1-flash-lite"
            elif ai_prov == "openai":
                ai_base = "https://api.openai.com/v1"
                ai_model = "gpt-4o-mini"
            elif ai_prov == "ollama":
                ai_base = "http://localhost:11434/v1"
                ai_model = "llama3"
            else:
                ai_base = input(f"{BOLD}Enter Base URL: {RESET}").strip()
                ai_model = input(f"{BOLD}Enter Model Name: {RESET}").strip()
                
        local_client_setup(
            server_ip=server_ip,
            token=token,
            server_mac=server_mac,
            tg_token=tg_token,
            chat_id=chat_id,
            ai_prov=ai_prov,
            ai_key=ai_key,
            ai_base=ai_base,
            ai_model=ai_model
        )
        print_header("Client Setup Complete!")
        print_success("Client configured and running.")
        
    elif choice == '3':
        local_server_setup_standalone()
    else:
        print_error("Invalid selection.")

if __name__ == "__main__":
    try:
        main_wizard()
    except KeyboardInterrupt:
        print("\nSetup cancelled.")
