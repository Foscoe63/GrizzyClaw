"""Text-to-speech utilities for voice interaction"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def speak_text(
    text: str,
    provider: str = "auto",
    elevenlabs_api_key: Optional[str] = None,
    elevenlabs_voice_id: Optional[str] = None,
) -> bool:
    """
    Speak text using available TTS backend.
    Providers: "elevenlabs", "pyttsx3", "say", "auto" (try ElevenLabs → pyttsx3 → say).

    Returns:
        True if speech succeeded, False otherwise.
    """
    if not text or not text.strip():
        return False

    # ElevenLabs (real-time, high quality)
    if provider in ("elevenlabs", "auto") and elevenlabs_api_key:
        try:
            return _speak_elevenlabs(
                text, elevenlabs_api_key, elevenlabs_voice_id or "21m00Tcm4TlvDq8ikWAM"
            )
        except Exception as e:
            logger.debug(f"ElevenLabs TTS failed: {e}")
            if provider == "elevenlabs":
                return False

    if provider == "auto" or provider == "pyttsx3":
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


def _speak_elevenlabs(
    text: str, api_key: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM"
) -> bool:
    """Speak via ElevenLabs API (streaming audio)."""
    import json
    import urllib.request

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = json.dumps({"text": text[:5000], "model_id": "eleven_monolingual_v1"}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        audio_bytes = r.read()
    if not audio_bytes:
        return False
    # Play on macOS via afplay
    import platform
    import subprocess
    import tempfile

    if platform.system() == "Darwin":
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_bytes)
            path = f.name
        try:
            subprocess.run(["afplay", path], check=True, capture_output=True, timeout=60)
            return True
        finally:
            import os
            try:
                os.unlink(path)
            except OSError:
                pass
    return False


def is_tts_available(elevenlabs_api_key: Optional[str] = None) -> bool:
    """Check if TTS is available (ElevenLabs, pyttsx3, or macOS say)."""
    if elevenlabs_api_key:
        return True
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
