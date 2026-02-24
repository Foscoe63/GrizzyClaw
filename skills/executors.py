"""Execute built-in skills: Google Calendar, Gmail (send/reply), GitHub (PRs/issues), MCP Marketplace."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default ClawHub-style MCP server list (when no marketplace URL is set)
DEFAULT_MCP_MARKETPLACE = [
    {"name": "playwright-mcp", "command": "npx", "args": ["-y", "@anthropic-ai/playwright-mcp"], "description": "Browser automation"},
    {"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."], "description": "Read/write files"},
    {"name": "ddg-search", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-ddg-search"], "description": "DuckDuckGo search"},
    {"name": "github", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "description": "GitHub repos, issues, PRs"},
]


# Standard token endpoint for Google OAuth 2.0
_GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Calendar scope - must be in token (and in creds scopes) for Calendar API
_CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"


def _get_google_creds(settings: Any):
    """Load Google OAuth credentials (Gmail/Calendar)."""
    path = getattr(settings, "gmail_credentials_json", None)
    if not path:
        return None
    from grizzyclaw.automation.gmail_creds import load_gmail_credentials
    secret = getattr(settings, "secret_key", None)
    data = load_gmail_credentials(str(Path(path).expanduser()), secret)
    if not data:
        return None
    # Normalize for from_authorized_user_info: token_uri and scopes (Playground uses "scope" string)
    data = dict(data)
    if not data.get("token_uri"):
        data["token_uri"] = _GOOGLE_TOKEN_URI
    if "scopes" not in data and data.get("scope"):
        # OAuth response often has "scope" as space-separated string; library expects "scopes" list
        data["scopes"] = [s.strip() for s in str(data.pop("scope", "")).split() if s.strip()]
    return data


def execute_calendar(action: str, params: Dict[str, Any], settings: Any) -> str:
    """Google Calendar: list_events, create_event."""
    creds_data = _get_google_creds(settings)
    if not creds_data:
        return "❌ Calendar: Configure Gmail/Google credentials in Settings → Integrations (same OAuth; add Calendar scope if needed)."
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        # Ensure Calendar scope is in the list so refresh grants access to Calendar API
        cal_creds = dict(creds_data)
        scopes = list(cal_creds.get("scopes") or [])
        if _CALENDAR_SCOPE not in scopes:
            scopes.append(_CALENDAR_SCOPE)
        cal_creds["scopes"] = scopes
        creds = Credentials.from_authorized_user_info(cal_creds)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("calendar", "v3", credentials=creds)
        calendar_id = params.get("calendarId", "primary")

        if action == "list_events":
            time_min = params.get("timeMin") or params.get("time_min")
            time_max = params.get("timeMax") or params.get("time_max")
            max_results = int(params.get("maxResults", params.get("max_results", 10)))
            request = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            events = request.execute().get("items", [])
            if not events:
                return "📅 No upcoming events."
            lines = ["📅 **Upcoming events:**"]
            for e in events:
                start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "?")
                lines.append(f"- **{e.get('summary', 'No title')}** — {start}")
            return "\n".join(lines)

        if action == "create_event":
            summary = params.get("summary", "Event")
            start = params.get("start") or params.get("startDateTime")
            end = params.get("end") or params.get("endDateTime")
            if not start or not end:
                return "❌ create_event requires start and end (e.g. 2026-02-20T10:00:00, 2026-02-20T11:00:00)."
            body = {
                "summary": summary,
                "start": {"dateTime": start, "timeZone": params.get("timezone", "UTC")},
                "end": {"dateTime": end, "timeZone": params.get("timezone", "UTC")},
            }
            if params.get("description"):
                body["description"] = params["description"]
            event = service.events().insert(calendarId=calendar_id, body=body).execute()
            return f"✅ Event created: **{event.get('summary', summary)}** — {start}"
        return f"❌ Unknown calendar action: {action}. Use list_events or create_event."
    except Exception as e:
        err_str = str(e).lower()
        if "invalid_grant" in err_str or "bad request" in err_str:
            return (
                "❌ Calendar error: invalid_grant — refresh token doesn't match your OAuth client. "
                "Use the same Client ID and secret in the Playground as in your token file, then do a fresh Authorize → Exchange and update the file."
            )
        if "403" in str(e) and ("insufficient" in err_str or "permission" in err_str or "scope" in err_str):
            return (
                "❌ Calendar error: token missing Calendar scope. In OAuth 2.0 Playground: add scope "
                "https://www.googleapis.com/auth/calendar (under Google Calendar API v3), then click "
                "Authorize APIs (sign in again), then Exchange authorization code for tokens. Replace the "
                "refresh_token in ~/.grizzyclaw/gmail_token.json with the new one. Quit and restart the app."
            )
        logger.exception("Calendar skill error")
        return f"❌ Calendar error: {e}"


def execute_gmail(action: str, params: Dict[str, Any], settings: Any) -> str:
    """Gmail: send_email, reply, list_messages (full send/reply)."""
    creds_data = _get_google_creds(settings)
    if not creds_data:
        return "❌ Gmail: Configure Gmail credentials in Settings → Integrations."
    try:
        from email.mime.text import MIMEText
        import base64
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_info(creds_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)

        if action == "send_email":
            to = params.get("to", "")
            subject = params.get("subject", "")
            body = params.get("body", params.get("text", ""))
            if not to:
                return "❌ send_email requires 'to'."
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"✅ Email sent to **{to}** — {subject}"

        if action == "reply":
            thread_id = params.get("thread_id") or params.get("threadId")
            message_id = params.get("message_id")
            body = params.get("body", params.get("text", ""))
            if not body:
                return "❌ reply requires 'body'."
            if thread_id:
                # Get one message in thread to get threadId for send
                thread = service.users().threads().get(userId="me", id=thread_id).execute()
                first_msg_id = thread["messages"][0]["id"]
                msg = service.users().messages().get(userId="me", id=first_msg_id, format="metadata").execute()
                headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                reply_msg = MIMEText(body)
                reply_msg["to"] = headers.get("From", "")
                reply_msg["subject"] = ("Re: " + headers["Subject"]) if "Subject" in headers else "Re:"
                reply_msg["In-Reply-To"] = headers.get("Message-ID", "")
                reply_msg["References"] = headers.get("References", headers.get("Message-ID", ""))
                raw = base64.urlsafe_b64encode(reply_msg.as_bytes()).decode()
                sent = service.users().messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()
                return "✅ Reply sent."
            if message_id:
                msg = service.users().messages().get(userId="me", id=message_id, format="metadata").execute()
                tid = msg.get("threadId")
                headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
                reply_msg = MIMEText(body)
                reply_msg["to"] = headers.get("From", "")
                reply_msg["subject"] = ("Re: " + headers["Subject"]) if "Subject" in headers else "Re:"
                reply_msg["In-Reply-To"] = headers.get("Message-ID", "")
                raw = base64.urlsafe_b64encode(reply_msg.as_bytes()).decode()
                service.users().messages().send(userId="me", body={"raw": raw, "threadId": tid}).execute()
                return "✅ Reply sent."
            return "❌ reply requires thread_id or message_id."
        if action == "list_messages":
            max_results = int(params.get("maxResults", params.get("max_results", 10)))
            q = params.get("q", "in:inbox")
            result = service.users().messages().list(userId="me", maxResults=max_results, q=q).execute()
            messages = result.get("messages", [])
            if not messages:
                return "📧 No messages."
            lines = ["📧 **Recent messages:**"]
            for m in messages[:max_results]:
                meta = service.users().messages().get(userId="me", id=m["id"], format="metadata").execute()
                headers = {h["name"]: h["value"] for h in meta["payload"].get("headers", [])}
                subj = headers.get("Subject", "(no subject)")
                lines.append(f"- **{subj}**")
            return "\n".join(lines)
        return f"❌ Unknown gmail action: {action}. Use send_email, reply, or list_messages."
    except Exception as e:
        err_str = str(e).lower()
        if "invalid_grant" in err_str or "bad request" in err_str:
            logger.warning("Gmail invalid_grant: %s", e)
            return (
                "❌ Gmail error: invalid_grant — refresh token doesn't match your client. "
                "Fix: 1) In Google Cloud Console open the same OAuth client you use in the Playground. "
                "2) Copy its Client ID and Client secret. 3) In OAuth 2.0 Playground (gear icon) enter those exact values. "
                "4) Select Gmail scopes → Authorize APIs → sign in as your test user → Exchange authorization code for tokens (once). "
                "5) In your token file put that client_id, that client_secret, and the refresh_token from the response. All three must be from the same flow."
            )
        logger.exception("Gmail skill error")
        return f"❌ Gmail error: {e}"


def execute_github(action: str, params: Dict[str, Any], settings: Any) -> str:
    """GitHub: list_prs, list_issues, create_issue, get_pr."""
    token = getattr(settings, "github_token", None)
    if not token:
        return "❌ GitHub: Set GITHUB_TOKEN in Settings → Integrations."
    try:
        from github import Github
        gh = Github(token)
        repo_spec = params.get("repo", "")
        if not repo_spec and action not in ("list_repos",):
            return "❌ GitHub action requires 'repo' (owner/name)."
        repo = gh.get_repo(repo_spec) if repo_spec else None

        if action == "list_prs":
            state = params.get("state", "open")
            prs = list(repo.get_pulls(state=state)[:int(params.get("limit", 10))])
            if not prs:
                return f"📋 No {state} PRs in **{repo_spec}**."
            lines = [f"📋 **PRs ({repo_spec}):**"]
            for pr in prs:
                lines.append(f"- #{pr.number} **{pr.title}** — {pr.state} by {pr.user.login}")
            return "\n".join(lines)

        if action == "list_issues":
            state = params.get("state", "open")
            issues = list(repo.get_issues(state=state)[:int(params.get("limit", 10))])
            if not issues:
                return f"📋 No {state} issues in **{repo_spec}**."
            lines = [f"📋 **Issues ({repo_spec}):**"]
            for iss in issues:
                lines.append(f"- #{iss.number} **{iss.title}** — {iss.state}")
            return "\n".join(lines)

        if action == "create_issue":
            title = params.get("title", "")
            body = params.get("body", params.get("description", ""))
            if not title:
                return "❌ create_issue requires 'title'."
            issue = repo.create_issue(title=title, body=body)
            return f"✅ Issue created: **#{issue.number}** — {issue.title}"

        if action == "get_pr":
            number = int(params.get("number", params.get("pr_number", 0)))
            if not number:
                return "❌ get_pr requires 'number'."
            pr = repo.get_pull(number)
            return f"**#{pr.number}** {pr.title}\nState: {pr.state}\nBy: {pr.user.login}\n\n{pr.body or ''}"

        return f"❌ Unknown github action: {action}. Use list_prs, list_issues, create_issue, get_pr."
    except Exception as e:
        logger.exception("GitHub skill error")
        return f"❌ GitHub error: {e}"


def execute_mcp_marketplace(action: str, params: Dict[str, Any], settings: Any) -> str:
    """MCP Marketplace: discover (list ClawHub MCP servers), install (add server to config)."""
    mcp_file = Path(getattr(settings, "mcp_servers_file", "~/.grizzyclaw/grizzyclaw.json")).expanduser()
    marketplace_url = getattr(settings, "mcp_marketplace_url", None)

    if action == "discover":
        servers: List[Dict[str, Any]] = []
        if marketplace_url:
            try:
                import urllib.request
                with urllib.request.urlopen(marketplace_url, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                servers = data.get("servers", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except Exception as e:
                logger.warning("MCP marketplace fetch failed: %s", e)
        if not servers:
            servers = DEFAULT_MCP_MARKETPLACE
        lines = ["🛒 **ClawHub MCP servers (install with skill mcp_marketplace, action install, params {\"name\": \"...\"}):**"]
        for s in servers:
            name = s.get("name", s.get("id", "?"))
            desc = s.get("description", "")
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)

    if action == "install":
        name = params.get("name", "").strip()
        if not name:
            return "❌ install requires 'name' (e.g. playwright-mcp, ddg-search)."
        # Resolve from default list or marketplace
        servers = DEFAULT_MCP_MARKETPLACE
        if marketplace_url:
            try:
                import urllib.request
                with urllib.request.urlopen(marketplace_url, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                servers = data.get("servers", data) if isinstance(data, dict) else (data if isinstance(data, list) else servers)
            except Exception:
                pass
        entry = next((s for s in servers if s.get("name", "").lower() == name.lower()), None)
        if not entry:
            return f"❌ Unknown MCP server '{name}'. Use discover to list available servers."
        mcp_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"mcpServers": {}}
        if mcp_file.exists():
            try:
                with open(mcp_file, "r") as f:
                    data = json.load(f)
                data.setdefault("mcpServers", {})
            except Exception:
                pass
        cmd = entry.get("command", "npx")
        args = entry.get("args", ["-y", name])
        data["mcpServers"][entry["name"]] = {"command": cmd, "args": args}
        with open(mcp_file, "w") as f:
            json.dump(data, f, indent=2)
        return f"✅ Installed MCP server **{entry['name']}**. Restart or refresh MCP in Settings → Skills & MCP to use it."
    return "❌ Unknown mcp_marketplace action. Use discover or install."