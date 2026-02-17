"""Text-to-speech utilities for voice interaction"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def speak_text(text: str) -> bool:
    """
    Speak text using available TTS backend.
    Tries pyttsx3 first (offline), then falls back to system say on macOS.

    Returns:
        True if speech succeeded, False otherwise.
    """
    if not text or not text.strip():
        return False
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        return True
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"pyttsx3 TTS failed: {e}")
    # Fallback: macOS say command
    try:
        import subprocess
        import platform
        if platform.system() == "Darwin":
            subprocess.run(
                ["say", "-r", "200", text[:5000]],
                check=True,
                capture_output=True,
                timeout=60,
            )
            return True
    except Exception as e:
        logger.debug(f"say fallback failed: {e}")
    return False


def is_tts_available() -> bool:
    """Check if TTS is available."""
    try:
        import pyttsx3
        return True
    except ImportError:
        pass
    try:
        import subprocess
        import platform
        if platform.system() == "Darwin":
            subprocess.run(["which", "say"], check=True, capture_output=True)
            return True
    except Exception:
        pass
    return False
