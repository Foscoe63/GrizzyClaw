"""Command-line interface for GrizzyClaw"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from grizzyclaw import __version__
from grizzyclaw.daemon.manager import ServiceManager
from grizzyclaw.daemon.ipc import IPCClient
from grizzyclaw.daemon.service import DaemonService

logger = logging.getLogger(__name__)


def daemon_command(args):
    """Handle daemon management commands"""
    manager = ServiceManager()

    if args.daemon_action == "start":
        print("Starting GrizzyClaw daemon...")
        if manager.start():
            print("✓ Daemon started successfully")
        else:
            print("✗ Failed to start daemon")
            sys.exit(1)

    elif args.daemon_action == "stop":
        print("Stopping GrizzyClaw daemon...")
        if manager.stop():
            print("✓ Daemon stopped successfully")
        else:
            print("✗ Failed to stop daemon")
            sys.exit(1)

    elif args.daemon_action == "restart":
        print("Restarting GrizzyClaw daemon...")
        if manager.restart():
            print("✓ Daemon restarted successfully")
        else:
            print("✗ Failed to restart daemon")
            sys.exit(1)

    elif args.daemon_action == "status":
        status = manager.status()
        print(f"Daemon status: {status}")

    elif args.daemon_action == "install":
        print("Installing GrizzyClaw daemon as system service...")
        if manager.install():
            print("✓ Daemon installed successfully")
            print("  The daemon will now start automatically on boot")
        else:
            print("✗ Failed to install daemon")
            sys.exit(1)

    elif args.daemon_action == "uninstall":
        print("Uninstalling GrizzyClaw daemon...")
        if manager.uninstall():
            print("✓ Daemon uninstalled successfully")
        else:
            print("✗ Failed to uninstall daemon")
            sys.exit(1)

    elif args.daemon_action == "run":
        # Run daemon in foreground (for debugging)
        print("Running GrizzyClaw daemon in foreground...")
        print("Press Ctrl+C to stop")
        daemon = DaemonService()
        try:
            asyncio.run(daemon.start())
        except KeyboardInterrupt:
            print("\n✓ Daemon stopped")


async def send_daemon_command(command: str, **kwargs):
    """Send a command to the running daemon"""
    client = IPCClient()

    if not client.is_daemon_running():
        print("✗ Daemon is not running")
        print("  Start it with: grizzyclaw daemon start")
        sys.exit(1)

    try:
        response = await client.send_command(command, **kwargs)
        return response
    except Exception as e:
        print(f"✗ Failed to communicate with daemon: {e}")
        sys.exit(1)


from grizzyclaw.gui.main_window import main as gui_main

def gui_command(args):
    """Launch the GUI"""
    gui_main()


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        prog="grizzyclaw",
        description="GrizzyClaw - 24/7 AI Assistant"
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # GUI command (default)
    gui_parser = subparsers.add_parser("gui", help="Launch GUI (default)")

    # Daemon management
    daemon_parser = subparsers.add_parser("daemon", help="Manage daemon service")
    daemon_parser.add_argument(
        "daemon_action",
        choices=["start", "stop", "restart", "status", "install", "uninstall", "run"],
        help="Daemon action"
    )

    # Version command
    version_parser = subparsers.add_parser("version", help="Show version")

    args, _ = parser.parse_known_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Handle commands
    if args.command == "daemon":
        daemon_command(args)
    elif args.command == "version":
        print(f"GrizzyClaw v{__version__}")
    else:
        # Default to GUI if no command specified
        gui_command(args)


if __name__ == "__main__":
    main()
