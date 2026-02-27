# GrizzyClaw

A secure, multi-platform AI agent combining OpenClaw's multi-channel messaging with memuBot's proactive memory system. Supports local LLMs (LM Studio, Ollama) with built-in web chatbot, Telegram and WhatsApp, voice chat, live visual canvas, skills/MCP, hooks and triggers, swarm (agent-to-agent delegation with leader/specialist roles), browser automation, scheduled tasks, daemon mode, and multi-agent workspaces.

## Features

🤖 **Multi-Platform**: Desktop app (macOS) + Telegram + WhatsApp
🧠 **Memory System**: Persistent conversation memory inspired by memuBot
🔒 **Security-First**: Encryption, JWT auth, rate limiting
⚡  **Local LLMs**: Native support for Ollama and LM Studio
🌐 **Cloud LLMs**: Also supports OpenAI, Anthropic, OpenRouter
🎤 **Voice Chat**: Record and transcribe voice messages; speak responses aloud (TTS)
🖼️ **Live Canvas**: Visual panel for images, screenshots, attachments, and agent-generated content
⚙️ **Skills & MCP**: Extensible capabilities (web search, filesystem, docs, browser) via ClawHub registry and Model Context Protocol
🔗 **Hooks & Triggers**: Incoming webhooks and message-based automation (e.g. when message contains "urgent" → fire webhook)
🌍 **Browser Automation**: Control web browsers via Playwright (navigate, screenshot, extract content, fill forms)
⏰ **Scheduled Tasks**: Cron-based task scheduler for automated reminders and actions
🔔 **Advanced Automation**: Gmail Pub/Sub integration and event-driven triggers
🔄 **Daemon Mode**: Run as 24/7 background service (launchd/systemd)
🗂️ **Multi-Agent Workspaces**: Isolated agent configurations with different LLMs, prompts, and memory; optional OpenAI Agents SDK + LiteLLM for coding; quality feedback (thumbs up/down) per workspace
🐝 **Swarm**: Agent-to-agent delegation with leader and specialist roles; use @workspace to delegate; Swarm Activity feed shows delegations and completions
🤖 **Sub-agents**: Agent can spawn background sub-agent runs (SPAWN_SUBAGENT); results announced in chat; list/kill in Sub-agents dialog; configurable depth and concurrency
📷 **Image Attachments**: Attach images to chat for vision-capable models
📋 **Memory Browser**: View, search, and manage conversation memories
👥 **Gateway Sessions**: When daemon runs, connect external clients via WebSocket; manage sessions from the GUI
📤 **Export**: Export conversations to Markdown or plain text
🎨 **Themed UI**: Customize theme, font, and appearance
📱 **Responsive UI**: Clean, modern macOS desktop interface
🖥️ **Shell Commands (Exec)**: OpenClaw-style command execution with user approval; safe-command allowlist, risky-pattern warnings, optional sandbox
💬 **Session Persistence**: Chat history saved per workspace across restarts; restore when switching workspaces
📐 **Compact Mode**: Tighter UI density in Settings → Appearance for smaller screens
🔬 **Workspace Benchmark**: Run 5-prompt speed tests per provider in Workspaces → Metrics
🔄 **Model Routing**: Optional smaller/faster model for simple tasks (e.g. list files) via config
✅ **Reliability**: LLM retries with backoff for transient failures; per-message queue timeout (default 5 min); clearer error messages; optional pre-send health check; exec approval dialog brought to front with dock alert; "Running command…" progress for long commands; graceful degradation when MCP servers are offline; structured tool/MCP logging (server, tool, duration, success)

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

- **Desktop GUI**: Launches the PyQt6 app with chat, voice input, live canvas, memory, scheduler, browser, sessions, workspaces, and swarm activity
- **Web UI** (when daemon runs): http://localhost:18788/chat
- **Control UI** (when daemon runs): http://localhost:18788/control  
  If you see "Control UI assets not found", build them with `pnpm ui:build` (auto-installs UI deps) or run `pnpm ui:dev` during development.
- **WebSocket Gateway**: ws://localhost:18789 for external clients
- **Telegram**: Message your bot (when daemon runs with `TELEGRAM_BOT_TOKEN`)

## Voice Input & Media

GrizzyClaw supports voice messages in the desktop GUI. Record your message, and it will be transcribed before being sent to the LLM.

### Transcription Providers
- **OpenAI**: Uses Whisper API (requires `OPENAI_API_KEY` in Settings → Integrations)
- **Local**: Uses Whisper on device (`pip install openai-whisper`, requires `ffmpeg`)

### Microphone Selection
If voice works from the terminal but not from the bundled app, select your microphone explicitly in **Settings → Integrations → Media & Transcription → Microphone**.

### Text-to-Speech
Click the 🔊 button on assistant messages to hear the response. Configure TTS provider in Settings → Integrations: **ElevenLabs** (high quality), **pyttsx3** (offline), or system **say** (macOS).

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

MCP servers are also defined in `~/.grizzyclaw/grizzyclaw.json`. For **fast-filesystem**, use `--allow` to specify allowed paths:

```json
{
  "mcpServers": {
    "fast-filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/fast-filesystem", "--allow", "/Users/ewg", "--allow", "/Volumes/Storage"]
    }
  }
}
```

Paths must match your OS (e.g. `/Users/...` on macOS). The agent normalizes common path mistakes (e.g. `/users/ewg` → `/Users/ewg`).

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
2. Load a model (use one trained for tool use, e.g. gpt-oss-120b, Qwen2.5-Instruct)
3. Start the local server (Developer → Local Server)
4. Configure URL in **Preferences → LLM Providers** (default: `http://localhost:1234/v1`; use `http://192.168.x.x:1234/v1` for remote LM Studio)

### OpenAI Agents SDK (Optional)

Workspaces can use the **OpenAI Agents SDK** with **LiteLLM** for improved coding workflows. Enable **Use Agents SDK (OpenAI + LiteLLM)** in the workspace LLM tab.

- **Providers**: Ollama, LM Studio, OpenAI, Anthropic, OpenRouter via LiteLLM
- **Max Turns**: Configurable per workspace (5–100, default 25) in the LLM tab
- **MCP Integration**: Tool name aliases and path/boolean coercion for MCP tools (e.g. fast-filesystem)
- **Dependencies**: `pip install openai-agents[litellm]` (see `requirements.txt`)

## Automation (Gmail Pub/Sub)

- **Gmail Pub/Sub**: Configure OAuth credentials and Pub/Sub topic in Settings → Integrations for push notifications on new emails.
- **Gmail skill** (list/reply): When the agent lists emails, only the **subject** is shown (no message id or thread id). Reply confirmations show "Reply sent." without exposing internal ids.

For webhooks and message-based triggers, see [Hooks & Triggers](#hooks--triggers) above.

## Configuration

See `.env.example` for environment variables. The GUI saves settings to `config.yaml` (project root when running from source, `~/.grizzyclaw/config.yaml` when running the bundled app). MCP servers are configured in `~/.grizzyclaw/grizzyclaw.json`.

### Required Settings

- `GRIZZYCLAW_SECRET_KEY`: Secret key for encryption
- At least one LLM provider (OLLAMA_URL, OPENAI_API_KEY, etc.)

### Optional Settings

- `TELEGRAM_BOT_TOKEN`: Get from @BotFather
- `WHATSAPP_SESSION_PATH`: For WhatsApp channel (optional)
- `DATABASE_URL`: Defaults to SQLite
- `TRANSCRIPTION_PROVIDER`: `openai` or `local` for voice input
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `None` to disable file logging
- `INPUT_DEVICE_NAME`: Microphone name (e.g. for bundled app compatibility)
- `CUSTOM_PROVIDER_URL`, `CUSTOM_PROVIDER_API_KEY`, `CUSTOM_PROVIDER_MODEL`: Custom OpenAI-compatible API
- `RULES_FILE`: Path to YAML file with custom rules (global default; workspaces can override)
- `EXEC_COMMANDS_ENABLED`: Allow shell commands (with approval); also in Settings → Security
- `EXEC_SAFE_COMMANDS_SKIP_APPROVAL`, `EXEC_SAFE_COMMANDS`: Safe commands that skip approval
- `EXEC_SANDBOX_ENABLED`: Run approved commands in restricted PATH (Settings → Security)
- `PRE_SEND_HEALTH_CHECK`: Ping LLM before sending; warn if unreachable (Settings → Security)
- `SESSION_PERSISTENCE`: Persist chat sessions to disk (default true)
- `SIMPLE_TASK_PROVIDER`, `SIMPLE_TASK_MODEL`: Use a smaller/faster model for simple tasks (config only)
- `LLM_RETRY_ATTEMPTS`: Number of retries for transient LLM failures (default 2; 3 attempts total)
- Agent queue per-message timeout is 5 minutes by default (configurable where `AgentQueue` is constructed)

## Building the macOS App

To create `GrizzyClaw.app` and `GrizzyClaw.dmg`:

```bash
./build_dmg.sh
```

Output:
- **App**: `dist/GrizzyClaw.app`
- **DMG**: `dist/GrizzyClaw.dmg`

Requires PyInstaller (`pip install pyinstaller`). The app includes microphone permission for voice input.

## Shell Commands (Exec)

When **Settings → Security → Allow shell commands** is enabled, the agent can run shell commands with your approval (OpenClaw-style).

### Flow
- The agent outputs `EXEC_COMMAND` with the command (and optional `cwd`). A popup asks you to **Approve** or **Reject** (dialog has comfortable height and spacing for multi-line commands).
- The dialog is brought to the front and the dock can alert so it’s not missed. While the command runs, the status bar shows **Running command…**.
- **Safe commands** (e.g. `ls`, `df`, `pwd`, `whoami`, `date`, `echo`) can skip approval when **Skip approval for safe commands** is enabled in Security.

### Safety
- **Risky pattern warnings**: The approval dialog highlights dangerous patterns (e.g. `rm -rf`, `sudo`, `curl | bash`, `wget | sh`, piping to shell) so you can double-check.
- **Optional sandbox**: Enable **Run approved commands in sandbox (restricted PATH)** in Security to run commands with `PATH=/usr/bin:/bin` only (best-effort restriction).

### Remote approval
From Telegram or Web, reply **approve** / **yes** / **run it** to run the pending command, or **reject** / **no** / **cancel** to cancel. Command history is stored in `~/.grizzyclaw/exec_history.json` and shown as “Recent commands” in the dialog.

## Session Persistence & Sessions Dialog

### Chat session persistence
- **Per-workspace chat history** is saved under `~/.grizzyclaw/sessions/` (e.g. `default_gui_user.json`).
- When you restart the app or switch workspaces, the chat view is restored from disk. Use **New Chat** (Ctrl+N) to clear and start fresh.
- **Agents SDK workspaces** (e.g. "Use Agents SDK" in the workspace LLM tab) now persist and restore sessions correctly when switching workspaces.
- Disable with config: `session_persistence: false` or env `SESSION_PERSISTENCE=false`.

### Sessions dialog (Gateway)
- The **👥 Sessions** item in the sidebar opens the **Gateway Sessions** dialog. It lists **sessions known to the daemon** (WebSocket gateway at `ws://127.0.0.1:18789`).
- If you don’t run the daemon or have no external clients connected, the list will be empty or show “Daemon not running or unreachable.” This is separate from your local chat history above.

## Reliability & UX

- **LLM retries**: Transient failures (timeout, connection errors) trigger up to 2 retries with backoff before showing a user-friendly "model temporarily unavailable" message. Configurable via `llm_retry_attempts` in config.
- **Queue timeout**: Each message has a maximum processing time (default 5 minutes). If the agent doesn't finish in time, you see "Request timed out. Please try again or use a shorter prompt." so the queue doesn't stay stuck.
- **MCP degradation**: When MCP tool discovery fails (e.g. servers offline), the agent is told "MCP tools were not discovered; you can still use skills and memory" so it can respond without tools.
- **Structured tool logging**: Tool/MCP calls are logged with server name, tool name, duration (ms), and success/failure for easier debugging.
- **Clearer error messages**: When the model returns no response or the LLM fails, you see specific guidance (e.g. Ollama still loading, LM Studio not running, timeout) instead of a generic message.
- **Pre-send health check** (optional): In **Settings → Security → Check LLM provider before sending**, the app pings the default LLM before sending. If it’s unreachable, you get a “Send anyway?” prompt.
- **Exec approval**: The command approval dialog is raised and activated, and the dock can bounce so it’s visible. Long-running approved commands show a “Running command…” status until the result streams back.
- **Compact mode**: **Settings → Appearance → Enable Compact Mode** reduces margins, spacing, and font size for a denser layout on smaller screens.

## Model Routing (Simple vs Complex)

You can route **simple** requests (e.g. “list files in …”, “what’s in this directory”, short Q&A) to a smaller/faster model:

- Set in config or env: `simple_task_provider` and `simple_task_model` (e.g. `ollama` and `llama3.2:3b`).
- When the user message is short, has no images, and matches simple patterns (list files, ls, pwd, etc.), the agent uses this provider/model instead of the default. No GUI for this yet; configure in `config.yaml` or environment.

## Security Features

- ✅ AES-256 encryption for sensitive data
- ✅ JWT token authentication
- ✅ Rate limiting
- ✅ Input validation
- ✅ SQL injection protection
- ✅ No secrets in logs
- ✅ Exec: approval required (with optional safe-command allowlist), risky-pattern warnings, optional sandbox

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
- WebSocket gateway (ws://127.0.0.1:18789) for external clients
- HTTP server for Web Chat and Control UI (http://127.0.0.1:18788)
- IPC server for CLI/GUI communication
- Sessions management via GUI when daemon runs
- Logs at `~/.grizzyclaw/daemon.log`

## Multi-Agent Workspaces

Create isolated agent configurations with different LLMs, system prompts, API keys, and memory databases.

### GUI
Click the **🗂️ Workspaces** button in the sidebar to:
- Create new workspaces from templates (Default, Code Assistant, Writing, Research, Personal, Planning)
- Configure LLM provider and model per workspace
- Set custom system prompts
- Override API keys per workspace
- Switch between workspaces instantly
- **Metrics tab**: View message/session counts; click **Run Benchmark (5 prompts)** to measure LLM latency for the current provider

### Workspace Features
| Feature | Description |
|---------|-------------|
| **Isolated Memory** | Each workspace has its own SQLite database (or shared for swarm) |
| **Custom LLM** | Different provider/model per workspace |
| **System Prompt** | Specialized prompts per workspace (overrides global Settings prompt) |
| **Max Tokens** | Configurable per workspace (100 to model max); respects Ollama/LM Studio context length |
| **Agents SDK** | Optional: Use OpenAI Agents SDK + LiteLLM for coding; configurable max turns (5–100) |
| **API Keys** | Override global keys per workspace |
| **Quality Feedback** | Thumbs up/down on assistant messages; quality % shown in Usage and workspace metrics |
| **Swarm** | Leader + specialist roles; agent-to-agent delegation via @mentions |
| **Templates** | Pre-built configurations for common use cases |

### Templates
- **Default**: General-purpose assistant (swarm leader)
- **Code Assistant**: Programming and debugging (lower temperature; implements full plans with multiple files when given detailed specs)
- **Writing Assistant**: Creative writing (higher temperature)
- **Research Assistant**: Information gathering with web search
- **Personal Assistant**: Daily tasks and reminders
- **Planning Assistant**: Project planning, roadmaps, milestones, and strategy

### Workspace Storage
- Workspaces are stored in `~/.grizzyclaw/workspaces.json`
- Each workspace memory is stored in `~/.grizzyclaw/workspace_<id>.db` unless **shared memory** is enabled for swarm; then all workspaces on the same inter-agent channel share `~/.grizzyclaw/shared_memory_<channel>.db`

### Rules File & System Prompt
- **Workspace prompt overrides global**: Each workspace has its own system prompt (Workspaces → Edit → Prompt tab). This overrides the global prompt in Settings → Prompts when the workspace is active.
- **Rules File**: Workspaces can use a custom `RULES_FILE` (YAML) for domain-specific instructions. The file is loaded and appended to the system prompt as "FOLLOW THESE RULES:". Configure in Settings → Prompts (global) or per-workspace.

### Swarm (Agent-to-Agent & Leader)

Workspaces can form a **swarm** where one agent acts as **leader** and others as **specialists**. Configure in **Workspaces → Swarm** tab.

**Agent-to-agent delegation**
- Enable "Inter-agent chat" so workspaces can message each other
- In chat, use `@workspace_slug` or `@Workspace Name` to delegate (e.g. `@code_assistant analyze this code`, `@planning break down this project`)
- You see **"Delegating to @X…"** while the request is in progress; clear errors if the target workspace is not found or has inter-agent disabled
- Optional **inter-agent channel** (e.g. `swarm1`) so only workspaces on the same channel can message each other
- **Shared memory**: Workspaces on the same channel share one memory DB at `~/.grizzyclaw/shared_memory_<channel>.db` (or `shared_memory_default.db` if no channel is set)
- **Sidebar**: Workspaces with inter-agent enabled show a tooltip with their @slug (e.g. "Code Assistant — inter-agent: use @code_assistant to delegate")

**Leader role**
- Set a workspace as **leader** (e.g. Default template)
- **Auto-delegate**: When the leader replies with lines like `@research Research X.`, `@coding Code Y.`, or `@planning Plan Z.`, those delegations are executed automatically
- The leader’s system prompt is **injected with the current list of @mention slugs** for workspaces on the same channel, so it always knows which specialists are available
- Specialists receive **delegation context** (e.g. "[Delegated from workspace Default] Task: …") so they know who delegated and why
- **Consensus**: Enable "synthesize specialist replies" to have the leader combine specialist responses into one answer, with a **Sources: @a, @b** line; the last delegation set is stored for session continuity

**Specialist roles**
- Workspaces can be `specialist_coding`, `specialist_writing`, `specialist_research`, `specialist_personal`, `specialist_planning`
- Specialists focus on their domain and respond concisely
- The Default template is pre-configured as a swarm leader; Coding, Writing, Research, Personal, and Planning templates are specialists

**Swarm Activity**
- Open **Swarm Activity** from the sidebar or **View → Swarm activity** to see a live feed of swarm events (delegations, claims, consensus).
- Events include `subtask_available` (when a task is delegated), `subtask_claimed` (when a specialist claims it), `task_completed` (when a delegation finishes), and `consensus_ready` (when the leader synthesizes specialist replies).
- Both **user @mentions** (e.g. you type `@coding What is 2+2?`) and **leader auto-delegate** (when the leader’s reply contains @mentions) emit events, so all delegations appear in the feed. Click **Refresh** to load the latest events.

**Sub-agents (agent-spawned background runs)**
- Enable **Sub-agents** in **Workspaces → Edit → Swarm** tab for a workspace. The agent can then spawn background sub-agent runs by outputting `SPAWN_SUBAGENT = { "task": "…", "label": "…" }`.
- Sub-agents run in isolation; when they finish, the result is **announced** in the chat and shown in **Sub-agents** (sidebar or **View → Sub-agents**). You can list active and completed runs and **Kill** a running sub-agent.
- Policy: **max spawn depth** (e.g. 2 = main and one level of children can spawn), **max children per parent** (default 5), and optional **run timeout**. Nested spawns allow an orchestrator pattern (main → orchestrator sub-agent → workers).
- Swarm Activity also shows `subagent_started`, `subagent_completed`, and `subagent_failed` events.

## Sub-agents (Background Agent Runs)

GrizzyClaw agents can spawn background sub-agent runs to handle tasks asynchronously.

### Enabling Sub-agents
Enable **Sub-agents** in **Workspaces → Edit → Swarm** tab for a workspace.

### Spawning Sub-agents
The agent spawns a sub-agent by outputting:
```
SPAWN_SUBAGENT = { "task": "Analyze this dataset", "label": "data-analysis" }
```

### Viewing & Managing Sub-agents
- **Sub-agents dialog**: Click 🤖 Sub-agents in the sidebar or **View → Sub-agents**
- View active and completed runs
- **Kill** running sub-agents
- Results are **announced in chat** when complete

### Policy Settings
Configure in **Workspaces → Edit → Swarm** tab:
| Setting | Description | Default |
|---------|-------------|---------|
| **Max spawn depth** | How deep sub-agents can nest (e.g. 2 = main + one level) | 2 |
| **Max children per parent** | Maximum concurrent sub-agents per parent | 5 |
| **Run timeout** | Optional timeout for sub-agent runs | None |

### Swarm Activity Events
Sub-agent events appear in **Swarm Activity**:
- `subagent_started` — Sub-agent run began
- `subagent_completed` — Sub-agent finished successfully
- `subagent_failed` — Sub-agent encountered an error

## Additional Capabilities

- **Custom LLM Provider**: Add your own OpenAI-compatible API endpoint in Settings → LLM Providers.
- **Device Actions** (macOS): Camera capture, screen capture, and local notifications via device_actions.
- **Memory Browser**: Click 🧠 Memory in the sidebar to view, search, and manage stored memories.
- **Sessions**: When the daemon runs, click 👥 Sessions to view **Gateway** sessions (WebSocket clients) and send messages to them. Your local chat history is restored automatically per workspace; see [Session Persistence & Sessions Dialog](#session-persistence--sessions-dialog).
- **Usage Dashboard**: Click 📊 Usage for LLM token usage, per-workspace metrics (messages, avg response time, quality %), and cost estimates.
- **Swarm Activity**: Click the swarm icon in the sidebar or **View → Swarm activity** to see recent delegation and consensus events; see [Swarm (Agent-to-Agent & Leader)](#swarm-agent-to-agent--leader).
- **Sub-agents**: Click 🤖 Sub-agents in the sidebar or **View → Sub-agents** to see active and completed sub-agent runs (spawned by the agent via SPAWN_SUBAGENT); kill running ones or refresh the list.
- **Export Conversation**: Use File → Export (Ctrl+E) to save the current chat as Markdown or plain text.
- **Safety**: Content filtering and PII redaction in logs (configurable in Settings).

## Architecture

```
grizzyclaw/
├── agent/         # Core agent logic (incl. OpenAI Agents SDK runner, MCP wrapper)
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
├── workspaces/    # Multi-agent workspace management
├── safety/        # Content filtering, PII redaction
├── observability/ # Metrics, tracing, logging
└── device_actions/# Camera, screen capture, notifications (macOS)
```

## Inspired By

- [OpenClaw](https://github.com/openclaw/openclaw) - Multi-channel AI gateway
- [memuBot](https://github.com/NevaMind-AI/memU) - Proactive memory for AI agents

## License

MIT License
