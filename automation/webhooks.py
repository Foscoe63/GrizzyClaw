"""Webhook server for external triggers"""

import logging
import asyncio
from typing import Callable, Dict, Optional
from dataclasses import dataclass
from aiohttp import web
import hmac
import hashlib

logger = logging.getLogger(__name__)


@dataclass
class Webhook:
    """A webhook endpoint"""
    path: str
    handler: Callable
    secret: Optional[str] = None
    metadata: Dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class WebhookServer:
    """HTTP webhook server for external triggers

    Allows external services to trigger actions in GrizzyClaw
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 18790):
        """Initialize webhook server

        Args:
            host: Host to bind to
            port: Port to listen on (default: 18790)
        """
        self.host = host
        self.port = port
        self.webhooks: Dict[str, Webhook] = {}
        self.app = web.Application()
        self.runner = None
        self.site = None

        # Setup catch-all route
        self.app.router.add_post("/{path:.*}", self._handle_webhook)
        self.app.router.add_get("/webhooks", self._list_webhooks)

    def register(
        self,
        path: str,
        handler: Callable,
        secret: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Register a webhook

        Args:
            path: Webhook path (e.g., "/github", "/slack")
            handler: Async function to handle webhook
            secret: Optional secret for signature verification
            metadata: Additional metadata
        """
        # Normalize path
        if not path.startswith("/"):
            path = f"/{path}"

        webhook = Webhook(
            path=path,
            handler=handler,
            secret=secret,
            metadata=metadata or {}
        )

        self.webhooks[path] = webhook
        logger.info(f"Registered webhook: {path}")

    def unregister(self, path: str) -> bool:
        """Unregister a webhook

        Args:
            path: Webhook path

        Returns:
            True if removed, False if not found
        """
        if not path.startswith("/"):
            path = f"/{path}"

        if path in self.webhooks:
            del self.webhooks[path]
            logger.info(f"Unregistered webhook: {path}")
            return True
        return False

    async def start(self):
        """Start the webhook server"""
        logger.info(f"Starting webhook server on http://{self.host}:{self.port}")

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info(f"✓ Webhook server started")
        logger.info(f"  Listening at: http://{self.host}:{self.port}")

    async def stop(self):
        """Stop the webhook server"""
        if self.runner:
            await self.runner.cleanup()
        logger.info("✓ Webhook server stopped")

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook request"""
        path = "/" + request.match_info.get("path", "")

        logger.info(f"Webhook received: {path}")

        # Find matching webhook
        webhook = self.webhooks.get(path)
        if not webhook:
            logger.warning(f"Unknown webhook path: {path}")
            return web.json_response(
                {"error": "Webhook not found"},
                status=404
            )

        try:
            # Get request body
            body = await request.text()

            # Verify signature if secret is set
            if webhook.secret:
                signature = request.headers.get("X-Hub-Signature-256") or \
                          request.headers.get("X-Signature")

                if not self._verify_signature(body, signature, webhook.secret):
                    logger.warning(f"Invalid signature for webhook: {path}")
                    return web.json_response(
                        {"error": "Invalid signature"},
                        status=401
                    )

            # Parse JSON body
            try:
                import json
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {"body": body}

            # Execute handler
            logger.info(f"Executing webhook handler: {path}")

            result = None
            if asyncio.iscoroutinefunction(webhook.handler):
                result = await webhook.handler(data)
            else:
                result = webhook.handler(data)

            logger.info(f"✓ Webhook '{path}' handled successfully")

            # Return response
            if isinstance(result, dict):
                return web.json_response(result)
            elif result is not None:
                return web.Response(text=str(result))
            else:
                return web.json_response({"status": "ok"})

        except Exception as e:
            logger.error(f"Error handling webhook '{path}': {e}", exc_info=True)
            return web.json_response(
                {"error": str(e)},
                status=500
            )

    async def _list_webhooks(self, request: web.Request) -> web.Response:
        """List registered webhooks"""
        webhooks_list = [
            {
                "path": webhook.path,
                "has_secret": webhook.secret is not None,
                "metadata": webhook.metadata
            }
            for webhook in self.webhooks.values()
        ]

        return web.json_response({
            "webhooks": webhooks_list,
            "count": len(webhooks_list)
        })

    def _verify_signature(
        self,
        body: str,
        signature: Optional[str],
        secret: str
    ) -> bool:
        """Verify webhook signature

        Args:
            body: Request body
            signature: Signature from headers
            secret: Webhook secret

        Returns:
            True if signature is valid
        """
        if not signature:
            return False

        # Handle different signature formats
        if signature.startswith("sha256="):
            # GitHub-style signature
            expected = "sha256=" + hmac.new(
                secret.encode(),
                body.encode(),
                hashlib.sha256
            ).hexdigest()
        else:
            # Simple HMAC signature
            expected = hmac.new(
                secret.encode(),
                body.encode(),
                hashlib.sha256
            ).hexdigest()

        return hmac.compare_digest(signature, expected)

    def get_stats(self) -> Dict:
        """Get webhook statistics

        Returns:
            Statistics dictionary
        """
        return {
            "total_webhooks": len(self.webhooks),
            "webhooks": [
                {
                    "path": webhook.path,
                    "has_secret": webhook.secret is not None,
                    "metadata": webhook.metadata
                }
                for webhook in self.webhooks.values()
            ]
        }
