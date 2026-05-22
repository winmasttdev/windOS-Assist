# windOS Assist 🌪️💻

windOS Assist is an advanced, secure remote management and assistant system. It enables you to monitor, control, and execute tasks on client machines (running Windows or Linux) remotely from Telegram. It utilizes a centralized Linux server to coordinate connections and host an agentic AI assistant capable of tool execution (function calling).

---

## 🚀 Key Features

* **Client-Initiated WebSockets**: Clients connect to the server via persistent WebSocket connections. This bypasses client-side NAT, firewalls, and home routers with zero port forwarding required on client machines.
* **Agentic AI Assistant (Tool Calling)**: Integrated with custom endpoints (Google Gemini, OpenAI, Ollama, OpenRouter, etc.). The AI has memory of the conversation history and is system-prompted with local hardware details. It can execute system commands (`run_command`), capture screenshots (`take_screenshot`), retrieve stats (`get_system_stats`), or trigger power states autonomously when assigned tasks by the user.
* **Neofetch Cloud ASCII Greeting**: Rich, system-status cards rendered on commands containing current date, 24h formatted time, local weather (via Open-Meteo REST API), CPU and GPU specifications, and an ASCII-art cloud logo.
* **GPS-Jamming Proof Geolocation**: Uses IP-based network location queries (`ip-api.com`) to calculate distances between client and server without depending on GPS satellite signals.
* **Decentralized Fallover Bot**: If the server goes offline, the client logs a customized warning:
  > *"Warning: Your server either crashed, powered itself off, or just was tired from it's misery, want to send a magical packet to return it to work? (y/n)"*
  * If the user triggers it, the client broadcasts a Wake-on-LAN Magic Packet to boot the server back up.
  * If the server remains dead, the client spins up its own **local Fallback Telegram Bot** allowing you to continue controlling the client directly from Telegram. It automatically pauses polling to reconnect to the server, avoiding Telegram API token conflicts.
* **Windows & Linux Support**: Runs as a hidden, background startup task on Windows (using Task Scheduler and VBScript launcher) or as a native `systemd` daemon on Linux.

---

## 🛠️ Telegram Commands

* `/start` / `/help` / `/greet` - Show Neofetch ASCII cloud greeting with weather, statuses, and times.
* `/clients` - Display registered client machines and click to switch active target.
* `/wakeup` - Send a Wake-on-LAN Magic Packet to the active offline client (uses its registered MAC address).
* `/screenshot` - Capture a high-res image of the active client's screen and send it as a photo.
* `/terminal` or `/sh` - Enter interactive shell terminal mode. Every text message you send to the bot is piped to the active client's running shell process (`cmd.exe` or `sh`) and streamed back in real-time. Type `exit` to close the session.
* `/ai <prompt>` - Send a task or question to the AI assistant (e.g. `"/ai Take a screenshot on the gaming PC, check if Steam is running, and if not, launch it."`).
* `/ai_chat` - Toggle continuous AI conversation mode (where you don't need the `/ai` command prefix).
* `/power <shutdown|reboot|sleep>` - Remote control the power states of the client.
* `/files <dir>` - Browse client directories and check file sizes.
* `/download <path>` - Download files from the client.
* `/upload <path>` - Upload files to the client.

---

## ⚡ Installation & Wizard

Run the interactive `setup.py` installer directly on your local client machine (this Windows PC). The wizard can remotely configure your Linux server, sync files from GitHub, install all required dependencies, and register background startup hooks automatically.

### Requirements

On Windows:
* Python 3.8+ (Make sure Python is added to PATH).
* pip package manager.

### Running Setup

1. Open PowerShell or Command Prompt.
2. Navigate to the project directory:
   ```powershell
   cd "C:\Users\winmastt\.gemini\antigravity\scratch\windos-assist"
   ```
3. Launch the setup script:
   ```powershell
   python setup.py
   ```
4. Follow the interactive steps:
   * Choose **1 (Full Setup)**.
   * Enter your remote Linux Server IP, SSH Username, and Password (or path to Private Key).
   * Enter your Telegram Bot Token and Chat ID.
   * Select your preferred AI provider (e.g., Google Gemini) and configure the API key.
   * The setup script will automatically connect to your Linux server, upload `server.py`, install requirements, register it as a `systemd` service, detect the server's MAC address, download dependencies on your local Windows PC, and install the hidden Task Scheduler startup task.

---

## 📁 Project Structure

* [server/server.py](file:///C:/Users/winmastt/.gemini/antigravity/scratch/windos-assist/server/server.py) - WebSocket server daemon and Telegram controller.
* [client/client.py](file:///C:/Users/winmastt/.gemini/antigravity/scratch/windos-assist/client/client.py) - WebSocket client background client runner.
* [setup.py](file:///C:/Users/winmastt/.gemini/antigravity/scratch/windos-assist/setup.py) - Interactive installer wizard.
* `client_config.json` / `server_config.json` - Generated settings (git-ignored for security).

---

> [!TIP]
> Recommend setting this directory as the active workspace in your IDE for easier code editing and management.
