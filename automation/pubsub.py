"""Pub/Sub integration for Gmail and other providers"""

import base64
import json
import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def verify_pubsub_push(audience_url: str):
    """
    Return a verification callable for Google Pub/Sub push requests.

    When audience_url is set, verifies the JWT in Authorization: Bearer <token>.
    Returns True if valid or if audience_url is empty (verification disabled).

    Args:
        audience_url: Expected audience (push endpoint URL, e.g. https://.../gmail)

    Returns:
        Async callable(request) -> bool
    """
    async def _verify(request) -> bool:
        if not audience_url:
            return True
        auth = request.headers.get("Authorization") if hasattr(request, "headers") else None
        if not auth or not auth.startswith("Bearer "):
            logger.debug("Pub/Sub push: missing or invalid Authorization header")
            return False
        token = auth[7:].strip()
        try:
            from google.auth import jwt as google_jwt

            # Decode and verify (fetches certs from issuer automatically)
            _ = google_jwt.decode(token, audience=audience_url)
            return True
        except Exception as e:
            logger.debug(f"Pub/Sub JWT verification failed: {e}")
            return False

    return _verify


class PubSubRegistry:
    """Registry for Pub/Sub provider handlers (gmail, calendar, drive, etc.)"""

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

    def register(self, provider: str, handler: Callable):
        """Register a provider handler."""
        self._handlers[provider] = handler
        logger.info(f"Registered Pub/Sub handler: {provider}")

    def get_handler(self, provider: str) -> Optional[Callable]:
        """Get handler for provider."""
        return self._handlers.get(provider)

    def handle_push(self, provider: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Route push notification to provider handler.

        Args:
            provider: Provider name (e.g., "gmail")
            data: Parsed webhook payload

        Returns:
            Response dict for webhook
        """
        handler = self.get_handler(provider)
        if not handler:
            return {"status": "error", "error": f"Unknown provider: {provider}"}
        try:
            if callable(handler):
                import asyncio
                if asyncio.iscoroutinefunction(handler):
                    import asyncio
                    loop = asyncio.get_event_loop()
                    result = loop.run_until_complete(handler(data))
                else:
                    result = handler(data)
                return result if isinstance(result, dict) else {"status": "ok"}
        except Exception as e:
            logger.error(f"Pub/Sub handler error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}


def parse_pubsub_push(body: str) -> Optional[Dict[str, Any]]:
    """
    Parse Google Pub/Sub push message format.

    Format: {"message": {"data": "<base64>", "messageId": "...", ...}, "subscription": "..."}
    Decoded data may contain: {"emailAddress": "...", "historyId": "..."} for Gmail.

    Returns:
        Parsed message data or None
    """
    try:
        payload = json.loads(body) if body else {}
        message = payload.get("message", {})
        b64_data = message.get("data")
        if not b64_data:
            return None
        decoded = base64.b64decode(b64_data).decode("utf-8")
        return json.loads(decoded)
    except Exception as e:
        logger.debug(f"Failed to parse Pub/Sub push: {e}")
        return None


async def handle_gmail_push(
    data: Dict[str, Any],
    agent_callback: Callable,
    credentials_path: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Handle Gmail Pub/Sub push: parse notification, optionally fetch mail via Gmail API, forward to agent.

    Args:
        data: Parsed webhook body (may include message.data decoded)
        agent_callback: Async generator (user_id, message) -> yields response chunks
        credentials_path: Path to Gmail OAuth token JSON (optional, for full fetch)

    Returns:
        Response for webhook
    """
    # Decode Pub/Sub message.data (base64) to get emailAddress, historyId
    if "message" in data and "data" in data["message"]:
        try:
            b64 = data["message"]["data"]
            decoded = base64.b64decode(b64).decode("utf-8")
            inner = json.loads(decoded)
            if inner:
                data = inner
        except Exception as e:
            logger.debug(f"Could not decode Pub/Sub message: {e}")
    email_address = data.get("emailAddress", "")
    history_id = data.get("historyId", "")
    if not email_address:
        return {"status": "ignored", "reason": "No emailAddress in payload"}
    user_id = f"gmail:{email_address}"
    summary_text = f"Gmail notification for {email_address} (historyId: {history_id})"
    try:
        if credentials_path:
            summary_text = await _fetch_gmail_summary(
                credentials_path, history_id, summary_text,
                secret_key=secret_key,
            )
    except Exception as e:
        logger.debug(f"Gmail API fetch skipped: {e}")
    try:
        response_chunks = []
        async for chunk in agent_callback(user_id, summary_text):
            response_chunks.append(chunk)
        return {"status": "ok", "response": "".join(response_chunks)[:500]}
    except Exception as e:
        logger.error(f"Gmail agent callback error: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def _fetch_gmail_summary(
    creds_path: Any,
    history_id: str,
    fallback: str,
    secret_key: Optional[str] = None,
) -> str:
    """Fetch new mail summaries via Gmail API. Returns fallback on failure."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request

        if secret_key:
            from grizzyclaw.automation.gmail_creds import load_gmail_credentials
            token_data = load_gmail_credentials(str(creds_path), secret_key)
        else:
            with open(creds_path) as f:
                token_data = json.load(f)
        if not token_data:
            return fallback
        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        service = build("gmail", "v1", credentials=creds)
        history = (
            service.users()
            .history()
            .list(userId="me", startHistoryId=history_id, historyTypes=["messageAdded"])
            .execute()
        )
        messages = history.get("messages", [])
        if not messages:
            return fallback
        summaries = []
        for msg_ref in messages[:5]:
            msg_id = msg_ref.get("id")
            if not msg_id:
                continue
            msg = service.users().messages().get(userId="me", id=msg_id).execute()
            snippet = msg.get("snippet", "")[:200]
            subject = next(
                (h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"] == "Subject"),
                "(no subject)",
            )
            summaries.append(f"[{subject}] {snippet}")
        return "New Gmail activity:\n" + "\n".join(summaries)
    except Exception as e:
        logger.debug(f"Gmail fetch failed: {e}")
        return fallback
