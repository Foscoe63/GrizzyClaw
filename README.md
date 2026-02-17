# GrizzyClaw

A secure, multi-platform AI agent combining OpenClaw's multi-channel messaging with memuBot's proactive memory system. Supports local LLMs (LM Studio, Ollama) with built-in web chatbot, Telegram integration, voice chat, live visual canvas, skills/MCP, hooks and triggers, browser automation, scheduled tasks, daemon mode, and multi-agent workspaces.

## Features

🤖 **Multi-Platform**: Desktop app (macOS) + Telegram bot
🧠 **Memory System**: Persistent conversation memory inspired by memuBot
🔒 **Security-First**: Encryption, JWT auth, rate limiting
⚡ **Local LLMs**: Native support for Ollama and LM Studio
🌐 **Cloud LLMs**: Also supports OpenAI, Anthropic, OpenRouter
🎤 **Voice Chat**: Record and transcribe voice messages; speak responses aloud (TTS)
🖼️ **Live Canvas**: Visual panel for images, screenshots, attachments, and agent-generated content
⚙️ **Skills & MCP**: Extensible capabilities (web search, filesystem, docs, browser) via ClawHub registry and Model Context Protocol
🔗 **Hooks & Triggers**: Incoming webhooks and message-based automation (e.g. when message contains "urgent" → fire webhook)
🌍 **Browser Automation**: Control web browsers via Playwright (navigate, screenshot, extract content, fill forms)
⏰ **Scheduled Tasks**: Cron-based task scheduler for automated reminders and actions
🔔 **Advanced Automation**: Gmail Pub/Sub integration and event-driven triggers
🔄 **Daemon Mode**: Run as 24/7 background service (launchd/systemd)
🗂️ **Multi-Agent Workspaces**: Isolated agent configurations with different LLMs, prompts, and memory
📱 **Responsive UI**: Clean, modern macOS desktop interface

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Run GrizzyClaw

```bash
# Launch desktop GUI (default)
python -m grizzyclaw gui
# or simply:
python -m grizzyclaw

# Run daemon (web + Telegram 24/7 in background)
python -m grizzyclaw daemon run
```

### 4. Access the Interface

- **Desktop GUI**: Launches the PyQt6 app with chat, voice input, live canvas, and workspaces
- **Web UI** (when daemon runs): http://localhost:18788/chat
- **Telegram**: Message your bot (when daemon runs with `TELEGRAM_BOT_TOKEN`)

## Voice Input & Media

GrizzyClaw supports voice messages in the desktop GUI. Record your message, and it will be transcribed before being sent to the LLM.

### Transcription Providers
- **OpenAI**: Uses Whisper API (requires `OPENAI_API_KEY` in Settings → Integrations)
- **Local**: Uses Whisper on device (`pip install openai-whisper`, requires `ffmpeg`)

### Microphone Selection
If voice works from the terminal but not from the bundled app, select your microphone explicitly in **Settings → Integrations → Media & Transcription → Microphone**.

### Text-to-Speech
Click the 🔊 button on assistant messages to hear the response. Configure TTS provider (ElevenLabs, pyttsx3, or system `say`) in Settings → Integrations.

## Live Canvas

The desktop GUI includes a **Visual Canvas** panel alongside the chat. It displays:
- Images from browser screenshots and attachments
- Agent-generated visual content (A2UI)
- Diagrams and other visual outputs

Images you attach or capture during a session appear on the canvas for quick reference.

## Skills & MCP

GrizzyClaw supports extensible AI capabilities via **Skills** and **MCP (Model Context Protocol)** servers.

### Built-in Skills (ClawHub Registry)
Enable skills in **Settings → ClawHub & MCP**:
- **web_search**: Search the web via DuckDuckGo
- **filesystem**: Read, write, and manage files
- **documentation**: Query library docs via Context7
- **browser**: Navigate, screenshot, and interact with pages
- **memory**: Remember and recall across conversations
- **scheduler**: Schedule tasks and reminders

### MCP Servers
Add MCP servers for additional tools (e.g. database access, custom APIs). Configure in Settings → ClawHub & MCP.

## Hooks & Triggers

GrizzyClaw supports event-driven automation:

- **Incoming Webhooks**: POST to your webhook URL to trigger actions. Configure in Settings → Integrations.
- **Triggers**: Run actions when messages match conditions (e.g. "when message contains 'urgent' → fire webhook"). Manage via **Settings → Integrations → Manage Triggers**.

## LLM Setup

### Ollama (Recommended for Local)

```bash
# Install Ollama from https://ollama.com
ollama pull llama3.2
ollama serve
```

### LM Studio

1. Download from https://lmstudio.ai
2. Load a model
3. Start the local server (default: http://localhost:1234)

## Automation (Gmail Pub/Sub)

- **Gmail Pub/Sub**: Configure OAuth credentials and Pub/Sub topic in Settings → Integrations for push notifications on new emails.

For webhooks and message-based triggers, see [Hooks & Triggers](#hooks--triggers) above.

## Configuration

See `.env.example` for environment variables. The GUI saves settings to `config.yaml` (project root when running from source, `~/.grizzyclaw/config.yaml` when running the bundled app).

### Required Settings

- `GRIZZYCLAW_SECRET_KEY`: Secret key for encryption
- At least one LLM provider (OLLAMA_URL, OPENAI_API_KEY, etc.)

### Optional Settings

- `TELEGRAM_BOT_TOKEN`: Get from @BotFather
- `DATABASE_URL`: Defaults to SQLite
- `TRANSCRIPTION_PROVIDER`: `openai` or `local` for voice input
- `INPUT_DEVICE_NAME`: Microphone name (e.g. for bundled app compatibility)

## Building the macOS App

To create `GrizzyClaw.app` and `GrizzyClaw.dmg`:

```bash
./build_dmg.sh
```

Output:
- **App**: `dist/GrizzyClaw.app`
- **DMG**: `dist/GrizzyClaw.dmg`

Requires PyInstaller (`pip install pyinstaller`). The app includes microphone permission for voice input.

## Security Features

- ✅ AES-256 encryption for sensitive data
- ✅ JWT token authentication
- ✅ Rate limiting
- ✅ Input validation
- ✅ SQL injection protection
- ✅ No secrets in logs

## Development

```bash
# Run tests
pytest tests/ -v

# Type checking
mypy grizzyclaw/

# Linting
ruff check grizzyclaw/
ruff format grizzyclaw/

# Security scan
bandit -r grizzyclaw/
```

## Browser Automation

GrizzyClaw includes a headless browser automation system powered by Playwright. You can control it via chat or the dedicated Browser dialog in the GUI.

### Via Chat Commands
Ask the LLM to perform browser actions. It will use `BROWSER_ACTION` commands:

```
"Go to google.com and take a screenshot"
"What's on the current page?"
"Click the search button"
"Fill in the email field with test@example.com"
```

### Available Actions
- **navigate**: Go to a URL
- **screenshot**: Capture the current page (full page optional)
- **get_text**: Extract text content from page or element
- **get_links**: Get all links on the page
- **click**: Click an element by CSS selector
- **fill**: Fill a form field
- **scroll**: Scroll the page up or down

### Setup Browser Automation
```bash
pip install playwright
playwright install chromium
```

Screenshots are saved to `~/.grizzyclaw/screenshots/`

## Scheduled Tasks

Schedule automated tasks using cron expressions. Manage tasks via chat or the Scheduler dialog in the GUI.

### Via Chat Commands
```
"Remind me to check email every morning at 9"
"Schedule a daily report at 6 PM"
"Show my scheduled tasks"
"Delete task task_abc123"
```

### Cron Expression Format
`minute hour day month weekday`

| Expression | Description |
|------------|-------------|
| `0 9 * * *` | Every day at 9 AM |
| `*/30 * * * *` | Every 30 minutes |
| `0 */2 * * *` | Every 2 hours |
| `0 0 * * 1` | Every Monday at midnight |
| `0 0 1 * *` | First day of each month |

### Task Storage
Tasks are stored in memory while the app runs. When a task triggers, it saves a reminder to your memory database at `~/.grizzyclaw/grizzyclaw.db`.

## Daemon Mode (24/7 Background Service)

GrizzyClaw can run as a system daemon, starting automatically on boot and running continuously in the background.

### CLI Commands
```bash
# Install as system service (starts on boot)
grizzyclaw daemon install

# Uninstall system service
grizzyclaw daemon uninstall

# Start/stop/restart the daemon
grizzyclaw daemon start
grizzyclaw daemon stop
grizzyclaw daemon restart

# Check daemon status
grizzyclaw daemon status

# Run in foreground (for debugging)
grizzyclaw daemon run
```

### Platform Support
| Platform | Service Manager | Service File Location |
|----------|-----------------|----------------------|
| macOS | launchd | `~/Library/LaunchAgents/com.grizzyclaw.daemon.plist` |
| Linux | systemd | `~/.config/systemd/user/grizzyclaw.service` |

### Daemon Features
- Automatic restart on crash
- WebSocket gateway for external clients
- IPC server for CLI/GUI communication
- Logs at `~/.grizzyclaw/daemon.log`

## Multi-Agent Workspaces

Create isolated agent configurations with different LLMs, system prompts, API keys, and memory databases.

### GUI
Click the **🗂️ Workspaces** button in the sidebar to:
- Create new workspaces from templates (Default, Code Assistant, Writing, Research, Personal)
- Configure LLM provider and model per workspace
- Set custom system prompts
- Override API keys per workspace
- Switch between workspaces instantly

### Workspace Features
| Feature | Description |
|---------|-------------|
| **Isolated Memory** | Each workspace has its own SQLite database |
| **Custom LLM** | Different provider/model per workspace |
| **System Prompt** | Specialized prompts for different tasks |
| **API Keys** | Override global keys per workspace |
| **Templates** | Pre-built configurations for common use cases |

### Templates
- **Default**: General-purpose assistant
- **Code Assistant**: Programming and debugging (lower temperature, more tokens)
- **Writing Assistant**: Creative writing (higher temperature)
- **Research Assistant**: Information gathering with web search
- **Personal Assistant**: Daily tasks and reminders

### Workspace Storage
Workspaces are stored in `~/.grizzyclaw/workspaces.json`
Each workspace memory is stored in `~/.grizzyclaw/workspace_<id>.db`

## Architecture

```
grizzyclaw/
├── agent/         # Core agent logic
├── automation/    # Browser control, scheduler, triggers, webhooks
├── channels/      # Telegram, WhatsApp
├── daemon/        # 24/7 background service (launchd/systemd)
├── gateway/       # WebSocket gateway, HTTP server, web chat
├── gui/           # PyQt6 desktop interface
├── llm/           # LLM provider integrations (Ollama, LM Studio, OpenAI, etc.)
├── memory/        # Persistent memory system (SQLite)
├── media/         # Transcription, media lifecycle
├── utils/         # Audio recording, TTS, vision
├── web/           # Web interface
└── workspaces/    # Multi-agent workspace management
```

## Inspired By

- [OpenClaw](https://github.com/openclaw/openclaw) - Multi-channel AI gateway
- [memuBot](https://github.com/NevaMind-AI/memU) - Proactive memory for AI agents

## License

MIT License
