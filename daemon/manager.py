"""Service manager for installing/managing daemon as system service"""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ServiceManager:
    """Manage daemon as system service (launchd/systemd)"""

    def __init__(self):
        self.system = platform.system()
        self.service_name = "com.grizzyclaw.daemon"

    def install(self) -> bool:
        """Install daemon as system service

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Darwin":  # macOS
            return self._install_launchd()
        elif self.system == "Linux":
            return self._install_systemd()
        else:
            logger.error(f"Unsupported platform: {self.system}")
            return False

    def uninstall(self) -> bool:
        """Uninstall daemon service

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Darwin":
            return self._uninstall_launchd()
        elif self.system == "Linux":
            return self._uninstall_systemd()
        else:
            logger.error(f"Unsupported platform: {self.system}")
            return False

    def start(self) -> bool:
        """Start the daemon service

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Darwin":
            return self._launchd_command("load")
        elif self.system == "Linux":
            return self._systemd_command("start")
        return False

    def stop(self) -> bool:
        """Stop the daemon service

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Darwin":
            return self._launchd_command("unload")
        elif self.system == "Linux":
            return self._systemd_command("stop")
        return False

    def restart(self) -> bool:
        """Restart the daemon service

        Returns:
            True if successful, False otherwise
        """
        self.stop()
        return self.start()

    def status(self) -> str:
        """Get daemon status

        Returns:
            Status string
        """
        if self.system == "Darwin":
            return self._launchd_status()
        elif self.system == "Linux":
            return self._systemd_status()
        return "Unknown"

    def _install_launchd(self) -> bool:
        """Install macOS launchd service"""
        try:
            # Get Python executable and script path
            python_exe = sys.executable
            script_path = Path(__file__).parent / "service.py"

            # Create plist content
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{self.service_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.grizzyclaw/daemon.out.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.grizzyclaw/daemon.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""

            # Write plist file
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"{self.service_name}.plist"
            plist_path.parent.mkdir(parents=True, exist_ok=True)

            with open(plist_path, "w") as f:
                f.write(plist_content)

            logger.info(f"Created launchd plist at {plist_path}")

            # Load the service
            subprocess.run(["launchctl", "load", str(plist_path)], check=True)
            logger.info("Daemon service installed and started")
            return True

        except Exception as e:
            logger.error(f"Failed to install launchd service: {e}")
            return False

    def _uninstall_launchd(self) -> bool:
        """Uninstall macOS launchd service"""
        try:
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"{self.service_name}.plist"

            if plist_path.exists():
                # Unload first
                subprocess.run(["launchctl", "unload", str(plist_path)])

                # Remove plist file
                plist_path.unlink()
                logger.info("Daemon service uninstalled")
            else:
                logger.warning("Service not installed")

            return True

        except Exception as e:
            logger.error(f"Failed to uninstall launchd service: {e}")
            return False

    def _launchd_command(self, command: str) -> bool:
        """Execute launchd command"""
        try:
            plist_path = Path.home() / "Library" / "LaunchAgents" / f"{self.service_name}.plist"
            subprocess.run(["launchctl", command, str(plist_path)], check=True)
            return True
        except Exception as e:
            logger.error(f"launchd {command} failed: {e}")
            return False

    def _launchd_status(self) -> str:
        """Get launchd service status"""
        try:
            result = subprocess.run(
                ["launchctl", "list", self.service_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return "running"
            else:
                return "stopped"
        except Exception:
            return "unknown"

    def _install_systemd(self) -> bool:
        """Install Linux systemd service"""
        try:
            # Get Python executable and script path
            python_exe = sys.executable
            script_path = Path(__file__).parent / "service.py"

            # Create service content
            service_content = f"""[Unit]
Description=GrizzyClaw AI Assistant Daemon
After=network.target

[Service]
Type=simple
User={os.getenv('USER')}
ExecStart={python_exe} {script_path}
Restart=always
RestartSec=10
StandardOutput=append:{Path.home()}/.grizzyclaw/daemon.out.log
StandardError=append:{Path.home()}/.grizzyclaw/daemon.err.log

[Install]
WantedBy=default.target
"""

            # Write service file (user service)
            service_path = Path.home() / ".config" / "systemd" / "user" / "grizzyclaw.service"
            service_path.parent.mkdir(parents=True, exist_ok=True)

            with open(service_path, "w") as f:
                f.write(service_content)

            logger.info(f"Created systemd service at {service_path}")

            # Reload systemd and enable service
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "grizzyclaw.service"], check=True)
            subprocess.run(["systemctl", "--user", "start", "grizzyclaw.service"], check=True)

            logger.info("Daemon service installed and started")
            return True

        except Exception as e:
            logger.error(f"Failed to install systemd service: {e}")
            return False

    def _uninstall_systemd(self) -> bool:
        """Uninstall Linux systemd service"""
        try:
            # Stop and disable service
            subprocess.run(["systemctl", "--user", "stop", "grizzyclaw.service"])
            subprocess.run(["systemctl", "--user", "disable", "grizzyclaw.service"])

            # Remove service file
            service_path = Path.home() / ".config" / "systemd" / "user" / "grizzyclaw.service"
            if service_path.exists():
                service_path.unlink()

            subprocess.run(["systemctl", "--user", "daemon-reload"])
            logger.info("Daemon service uninstalled")
            return True

        except Exception as e:
            logger.error(f"Failed to uninstall systemd service: {e}")
            return False

    def _systemd_command(self, command: str) -> bool:
        """Execute systemd command"""
        try:
            subprocess.run(["systemctl", "--user", command, "grizzyclaw.service"], check=True)
            return True
        except Exception as e:
            logger.error(f"systemd {command} failed: {e}")
            return False

    def _systemd_status(self) -> str:
        """Get systemd service status"""
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "grizzyclaw.service"],
                capture_output=True,
                text=True
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"
