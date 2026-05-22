import os
import sys
import json
import socket
import secrets
import subprocess
import paramiko

# Configuration
SERVER_IP = "192.168.0.102"
SSH_USER = "winmastt"
SSH_PASS = "321654"
TG_TOKEN = "8932933678:AAHLyew625TKRXucnUPsoElTwMi8u0jRQXk"
CHAT_ID = 6558418835
CLIENT_IP = "192.168.0.103"
OLLAMA_MODEL = "qwen:latest"

# Generate client authorization token
CLIENT_TOKEN = secrets.token_hex(16)

def run_ssh_command(ssh, cmd, sudo=False, password=None):
    if sudo and password:
        cmd = f"echo '{password}' | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    return exit_status, out, err

def main():
    print("=== Automated windOS Assist Setup ===")
    
    # 1. Establish SSH connection
    print(f"Connecting to remote server at {SERVER_IP}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(SERVER_IP, username=SSH_USER, password=SSH_PASS, timeout=10)
        print("[SUCCESS] SSH connected successfully!")
    except Exception as e:
        print(f"[ERROR] SSH Connection failed: {e}")
        sys.exit(1)
        
    # 2. Get server MAC address
    print("Detecting server MAC address...")
    _, out, _ = run_ssh_command(ssh, "ip route show | grep default | awk '{print $5}'")
    iface = out.strip()
    _, out, _ = run_ssh_command(ssh, f"cat /sys/class/net/{iface}/address")
    server_mac = out.strip().upper()
    if not server_mac or len(server_mac) != 17:
        # Fallback MAC lookup
        _, out, _ = run_ssh_command(ssh, "ip link show | grep -o 'ether [0-9a-f:]*' | head -n 1 | awk '{print $2}'")
        server_mac = out.strip().upper()
        
    print(f"[SUCCESS] Server MAC address: {server_mac}")
    
    # 3. Create server directories and upload server.py
    print("Creating directory /opt/windos-assist/server...")
    run_ssh_command(ssh, "mkdir -p /opt/windos-assist/server", sudo=True, password=SSH_PASS)
    run_ssh_command(ssh, f"chown -R {SSH_USER}:{SSH_USER} /opt/windos-assist", sudo=True, password=SSH_PASS)
    
    server_local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "server.py")
    print("Uploading server.py to remote server...")
    try:
        sftp = ssh.open_sftp()
        sftp.put(server_local_path, "/opt/windos-assist/server/server.py")
        sftp.close()
        print("[SUCCESS] Uploaded server.py successfully!")
    except Exception as e:
        print(f"[ERROR] Upload failed: {e}")
        ssh.close()
        sys.exit(1)
        
    # 4. Write server_config.json remotely
    print("Configuring remote server settings...")
    server_config = {
        "telegram_token": TG_TOKEN,
        "authorized_chat_id": CHAT_ID,
        "websocket_port": 8765,
        "websocket_host": "0.0.0.0",
        "client_token": CLIENT_TOKEN,
        "ai_provider": "ollama",
        "ai_api_key": "",
        "ai_base_url": f"http://{CLIENT_IP}:11434/v1",
        "ai_model": OLLAMA_MODEL,
        "server_mac": server_mac
    }
    
    config_json = json.dumps(server_config, indent=4)
    # Write remotely
    stdin, stdout, stderr = ssh.exec_command("cat > /opt/windos-assist/server/server_config.json")
    stdin.write(config_json)
    stdin.channel.shutdown_write()
    stdout.channel.recv_exit_status()
    print("[SUCCESS] Remote server settings configured!")
    
    # 5. Install dependencies on the server
    print("Installing packages on remote server...")
    run_ssh_command(ssh, "apt-get update && apt-get install -y python3-pip python3-venv", sudo=True, password=SSH_PASS)
    
    # Create virtual environment
    print("Creating virtual environment /opt/windos-assist/venv...")
    run_ssh_command(ssh, "python3 -m venv /opt/windos-assist/venv", sudo=True, password=SSH_PASS)
    run_ssh_command(ssh, f"chown -R {SSH_USER}:{SSH_USER} /opt/windos-assist/venv", sudo=True, password=SSH_PASS)
    
    # Install dependencies inside venv
    print("Installing dependencies in virtual environment...")
    run_ssh_command(ssh, "/opt/windos-assist/venv/bin/pip install websockets pyTelegramBotAPI aiohttp openai")
    print("[SUCCESS] Server dependencies installed in virtual environment!")
    
    # 6. Setup systemd service on Linux
    print("Creating systemd service for server daemon...")
    service_file = (
        "[Unit]\n"
        "Description=windOS Assist Server Daemon\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        "WorkingDirectory=/opt/windos-assist/server\n"
        "ExecStart=/opt/windos-assist/venv/bin/python /opt/windos-assist/server/server.py\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    
    stdin, stdout, stderr = ssh.exec_command("cat > /tmp/windos-assist-server.service")
    stdin.write(service_file)
    stdin.channel.shutdown_write()
    stdout.channel.recv_exit_status()
    
    # Move to systemd and enable/start
    run_ssh_command(ssh, "mv /tmp/windos-assist-server.service /etc/systemd/system/windos-assist-server.service", sudo=True, password=SSH_PASS)
    run_ssh_command(ssh, "systemctl daemon-reload", sudo=True, password=SSH_PASS)
    run_ssh_command(ssh, "systemctl enable windos-assist-server.service", sudo=True, password=SSH_PASS)
    run_ssh_command(ssh, "systemctl restart windos-assist-server.service", sudo=True, password=SSH_PASS)
    print("[SUCCESS] Remote server systemd daemon started!")
    
    ssh.close()
    
    # 7. Configure Local Client
    print("\n=== Configuring Local Client ===")
    client_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client")
    
    client_config = {
        "server_url": f"ws://{SERVER_IP}:8765",
        "client_token": CLIENT_TOKEN,
        "name": socket.gethostname(),
        "server_mac": server_mac,
        "telegram_token": TG_TOKEN,
        "authorized_chat_id": CHAT_ID,
        "ai_provider": "ollama",
        "ai_api_key": "",
        "ai_base_url": "http://127.0.0.1:11434/v1",
        "ai_model": OLLAMA_MODEL
    }
    
    config_path = os.path.join(client_dir, "client_config.json")
    with open(config_path, "w") as f:
        json.dump(client_config, f, indent=4)
    print(f"[SUCCESS] Saved client_config.json to {config_path}")
    
    # 8. Install local client dependencies
    print("Installing local Python dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "pillow", "psutil", "pyTelegramBotAPI", "aiohttp"])
        print("[SUCCESS] Local dependencies installed!")
    except Exception as e:
        print(f"[WARNING] Failed to install dependencies: {e}")
        
    # 9. Register startup persistence (copy VBS launcher to Windows Startup folder)
    print("Setting up Windows startup persistence...")
    vbs_path = os.path.join(client_dir, "run_client.vbs")
    client_script_path = os.path.join(client_dir, "client.py")
    
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "python ""{client_script_path}""", 0, False\n'
    )
    with open(vbs_path, "w") as f:
        f.write(vbs_content)
    print("[SUCCESS] Created run_client.vbs launcher.")
    
    # Put launcher in the Windows Startup folder for current user
    appdata = os.environ.get('APPDATA')
    if appdata:
        startup_dir = os.path.join(appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
        startup_vbs_path = os.path.join(startup_dir, 'windOS_Assist_Client.vbs')
        try:
            with open(startup_vbs_path, "w") as f:
                f.write(vbs_content)
            print(f"[SUCCESS] Registered startup script in Startup folder: {startup_vbs_path}")
        except Exception as e:
            print(f"[WARNING] Failed to write to Startup folder: {e}")
    else:
        print("[WARNING] APPDATA environment variable not found. Skipping Startup folder registration.")
        
    try:
        # Create Task Scheduler task as fallback
        task_name = "windOS_Assist_Client"
        cmd = f'schtasks /create /tn "{task_name}" /tr "wscript.exe \\"{vbs_path}\\"" /sc onlogon /f'
        subprocess.check_call(cmd, shell=True)
        print("[SUCCESS] Registered Windows Task Scheduler task!")
    except Exception as e:
        print(f"[INFO] Task Scheduler registration skipped/failed: {e} (Using Startup folder instead)")
        
    # 10. Start local client in background
    print("Launching client daemon in the background...")
    try:
        subprocess.Popen(f'wscript.exe "{vbs_path}"', shell=True)
        print("[SUCCESS] Client daemon running!")
    except Exception as e:
        print(f"[ERROR] Failed to start client daemon: {e}")
        
    print("\n=============================================")
    print("Setup is fully complete!")
    print("Go to Telegram and send /start to your bot!")
    print("=============================================")

if __name__ == "__main__":
    main()
