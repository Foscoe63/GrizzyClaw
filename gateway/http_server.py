"""HTTP server for serving WebChat and control UI"""

import asyncio
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)


class HTTPServer:
    """HTTP server for static files and API"""

    def __init__(self, host: str = "127.0.0.1", port: int = 18788):
        """Initialize HTTP server

        Args:
            host: Host to bind to
            port: Port to listen on
        """
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner = None
        self.site = None

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup HTTP routes"""
        static_dir = Path(__file__).parent / "static"

        # Static files
        self.app.router.add_static('/static', static_dir)

        # WebChat endpoint
        self.app.router.add_get('/', self.serve_webchat)
        self.app.router.add_get('/chat', self.serve_webchat)
        self.app.router.add_get('/webchat', self.serve_webchat)

        # Control UI (built assets or fallback with build instructions)
        self.app.router.add_get("/control", self.serve_control)
        self.app.router.add_get("/control/", self.serve_control)
        control_dir = static_dir / "control"
        if control_dir.exists():
            self.app.router.add_static("/control", control_dir)

        # API endpoints
        self.app.router.add_get('/api/health', self.health_check)
        self.app.router.add_get('/api/metrics', self.metrics)

    async def serve_webchat(self, request):
        """Serve WebChat HTML"""
        static_dir = Path(__file__).parent / "static"
        webchat_file = static_dir / "webchat.html"

        if webchat_file.exists():
            with open(webchat_file, 'r') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        else:
            return web.Response(text="WebChat not found", status=404)

    def _control_fallback_html(self) -> str:
        """HTML shown when Control UI assets are not built."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Control UI – GrizzyClaw</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eaeaea;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .card {
            max-width: 480px;
            background: #16213e;
            border-radius: 12px;
            padding: 28px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }
        h1 { font-size: 1.25rem; margin-bottom: 12px; color: #fff; }
        p { line-height: 1.6; margin-bottom: 16px; color: #b8b8b8; }
        code {
            background: #0f0f1a;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 0.9em;
        }
        .cmd { margin: 12px 0; }
        a { color: #7eb8da; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Control UI assets not found</h1>
        <p>Build them with <code>pnpm ui:build</code> (auto-installs UI deps), or run <code>pnpm ui:dev</code> during development.</p>
        <p class="cmd">From the project root:</p>
        <p><code>pnpm ui:build</code></p>
        <p><a href="/chat">Open Web Chat</a> · <a href="/api/health">Health</a></p>
    </div>
</body>
</html>"""

    async def serve_control(self, request):
        """Serve Control UI: built SPA if present, else fallback with build instructions."""
        static_dir = Path(__file__).parent / "static"
        control_index = static_dir / "control" / "index.html"

        if control_index.exists():
            with open(control_index, "r") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html")
        return web.Response(
            text=self._control_fallback_html(),
            content_type="text/html",
        )

    async def health_check(self, request):
        """Health check endpoint"""
        return web.json_response({
            "status": "ok",
            "service": "GrizzyClaw Gateway"
        })

    async def metrics(self, request):
        """Metrics endpoint (latency, tokens, error rates)"""
        try:
            from grizzyclaw.observability.metrics import get_metrics
            return web.json_response(get_metrics().get_stats())
        except Exception as e:
            logger.warning("Metrics endpoint failed: %s", e)
            return web.json_response({})

    async def start(self):
        """Start the HTTP server"""
        logger.info(f"Starting HTTP server on http://{self.host}:{self.port}")

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info("✓ HTTP server started")
        logger.info("  WebChat: http://%s:%s/chat", self.host, self.port)
        logger.info("  Control UI: http://%s:%s/control", self.host, self.port)

    async def stop(self):
        """Stop the HTTP server"""
        if self.runner:
            await self.runner.cleanup()
        logger.info("HTTP server stopped")
