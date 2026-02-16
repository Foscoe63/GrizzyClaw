"""HTTP server for serving WebChat and control UI"""

import asyncio
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger(__name__)


class HTTPServer:
    """HTTP server for static files and API"""

    def __init__(self, host: str = "127.0.0.1", port: int = 18789):
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
        except Exception:
            return web.json_response({})

    async def start(self):
        """Start the HTTP server"""
        logger.info(f"Starting HTTP server on http://{self.host}:{self.port}")

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()

        logger.info(f"✓ HTTP server started")
        logger.info(f"  WebChat: http://{self.host}:{self.port}/chat")

    async def stop(self):
        """Stop the HTTP server"""
        if self.runner:
            await self.runner.cleanup()
        logger.info("HTTP server stopped")
