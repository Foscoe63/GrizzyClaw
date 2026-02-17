"""Device-specific action implementations"""

import logging
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def capture_camera(output_path: Optional[str] = None) -> Optional[str]:
    """
    Capture image from system camera (macOS: screencapture of Photo Booth or via imagesnap).

    Returns:
        Path to captured image, or None on failure.
    """
    if platform.system() != "Darwin":
        logger.warning("Camera capture only supported on macOS")
        return None
    out = output_path or tempfile.mktemp(suffix=".png")
    try:
        # imagesnap is a common CLI for macOS camera (brew install imagesnap)
        subprocess.run(["which", "imagesnap"], check=True, capture_output=True)
        subprocess.run(["imagesnap", out], check=True, capture_output=True, timeout=10)
        return out
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # Fallback: use screencapture -w (interactive window selection) - not ideal
    try:
        subprocess.run(["screencapture", "-x", out], check=True, capture_output=True, timeout=5)
        return out
    except Exception as e:
        logger.debug(f"Camera capture failed: {e}")
    return None


def start_screen_recording(output_path: Optional[str] = None) -> Optional[str]:
    """
    Capture screen (macOS: screencapture). Full video recording requires
    companion app or third-party tool.

    Returns:
        Path to screenshot, or None.
    """
    if platform.system() != "Darwin":
        logger.warning("Screen capture only supported on macOS")
        return None
    out = output_path or tempfile.mktemp(suffix=".png")
    try:
        subprocess.run(["screencapture", "-x", out], check=True, capture_output=True, timeout=5)
        return out
    except Exception as e:
        logger.debug(f"Screen capture failed: {e}")
    return None


def get_location() -> Optional[dict]:
    """
    Get current location (lat, lon). Requires system permission.

    Returns:
        {"lat": float, "lon": float} or None.
    """
    if platform.system() != "Darwin":
        return None
    try:
        # macOS: CoreLocation via Python bridge - would need pyobjc
        # Stub: return None; implement via companion app
        return None
    except Exception:
        return None


def send_notification(title: str, body: str) -> bool:
    """
    Send a local notification (macOS: osascript, or NSUserNotification).

    Returns:
        True if sent.
    """
    if platform.system() != "Darwin":
        logger.warning("Notifications only supported on macOS")
        return False
    try:
        script = f'display notification "{body}" with title "{title}"'
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except Exception as e:
        logger.debug(f"Notification failed: {e}")
    return False
