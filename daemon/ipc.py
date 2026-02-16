"""Inter-process communication between daemon and GUI"""

import asyncio
import json
import logging
import socket
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class IPCServer:
    """IPC server running in daemon to receive commands"""

    def __init__(self, socket_path: Optional[str] = None):
        """Initialize IPC server

        Args:
            socket_path: Path to Unix socket, defaults to ~/.grizzyclaw/daemon.sock
        """
        self.socket_path = socket_path or str(Path.home() / ".grizzyclaw" / "daemon.sock")
        self.server: Optional[asyncio.Server] = None
        self.handlers: Dict[str, Callable] = {}

    def register_handler(self, command: str, handler: Callable):
        """Register a command handler

        Args:
            command: Command name (e.g., 'status', 'reload')
            handler: Async function to handle the command
        """
        self.handlers[command] = handler

    async def start(self):
        """Start the IPC server"""
        # Remove existing socket file if it exists
        socket_file = Path(self.socket_path)
        if socket_file.exists():
            socket_file.unlink()

        # Ensure directory exists
        socket_file.parent.mkdir(parents=True, exist_ok=True)

        # Start Unix socket server
        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=self.socket_path
        )

        logger.info(f"IPC server listening on {self.socket_path}")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming client connection"""
        try:
            # Read request
            data = await reader.read(4096)
            if not data:
                return

            request = json.loads(data.decode())
            command = request.get("command")
            args = request.get("args", {})

            logger.info(f"Received IPC command: {command}")

            # Execute handler
            if command in self.handlers:
                try:
                    result = await self.handlers[command](**args)
                    response = {"status": "success", "result": result}
                except Exception as e:
                    logger.error(f"Error handling command {command}: {e}")
                    response = {"status": "error", "error": str(e)}
            else:
                response = {"status": "error", "error": f"Unknown command: {command}"}

            # Send response
            writer.write(json.dumps(response).encode())
            await writer.drain()

        except Exception as e:
            logger.error(f"Error handling IPC client: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def stop(self):
        """Stop the IPC server"""
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        # Clean up socket file
        socket_file = Path(self.socket_path)
        if socket_file.exists():
            socket_file.unlink()

        logger.info("IPC server stopped")


class IPCClient:
    """IPC client for GUI to send commands to daemon"""

    def __init__(self, socket_path: Optional[str] = None):
        """Initialize IPC client

        Args:
            socket_path: Path to Unix socket, defaults to ~/.grizzyclaw/daemon.sock
        """
        self.socket_path = socket_path or str(Path.home() / ".grizzyclaw" / "daemon.sock")

    async def send_command(self, command: str, **args) -> Dict[str, Any]:
        """Send a command to the daemon

        Args:
            command: Command name
            **args: Command arguments

        Returns:
            Response from daemon

        Raises:
            ConnectionError: If daemon is not running
        """
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)

            # Send request
            request = {"command": command, "args": args}
            writer.write(json.dumps(request).encode())
            await writer.drain()

            # Read response
            data = await reader.read(4096)
            response = json.loads(data.decode())

            writer.close()
            await writer.wait_closed()

            return response

        except FileNotFoundError:
            raise ConnectionError("Daemon is not running")
        except Exception as e:
            raise ConnectionError(f"Failed to communicate with daemon: {e}")

    def is_daemon_running(self) -> bool:
        """Check if daemon is running

        Returns:
            True if daemon is running, False otherwise
        """
        socket_file = Path(self.socket_path)
        if not socket_file.exists():
            return False

        # Try to connect
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
            sock.close()
            return True
        except:
            return False
