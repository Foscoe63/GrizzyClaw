"""Device-specific actions: camera, screen recording, location, notifications

Companion apps/nodes on macOS, iOS, and Android can provide these.
This module exposes stubs and macOS implementations where available.
"""

from .actions import (
    capture_camera,
    start_screen_recording,
    get_location,
    send_notification,
)

__all__ = [
    "capture_camera",
    "start_screen_recording",
    "get_location",
    "send_notification",
]
