"""Audio transcription via Whisper (local) or OpenAI API"""

import base64
import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription fails."""


def transcribe_audio(
    source: Union[str, bytes],
    provider: str = "openai",
    openai_api_key: Optional[str] = None,
) -> str:
    """
    Transcribe audio from file path, bytes, or base64 string.

    Args:
        source: File path, raw bytes, or base64-encoded audio
        provider: "local" (Whisper) or "openai"
        openai_api_key: Required for provider="openai"

    Returns:
        Transcribed text, or empty string on failure
    """
    path: Optional[Path] = None
    cleanup_path = False

    try:
        if isinstance(source, bytes):
            fd, tmp = tempfile.mkstemp(suffix=".mp3")
            try:
                import os
                os.write(fd, source)
                os.close(fd)
                path = Path(tmp)
                cleanup_path = True
            except Exception:
                import os
                try:
                    os.close(fd)
                except OSError:
                    pass
                if path and path.exists():
                    path.unlink()
                raise
        elif isinstance(source, str):
            if source.startswith("data:"):
                # Data URL
                parts = source.split(",", 1)
                b64_data = parts[1] if len(parts) > 1 else ""
                raw = base64.b64decode(b64_data)
                return transcribe_audio(raw, provider=provider, openai_api_key=openai_api_key)
            path = Path(source).expanduser()
            if not path.exists():
                raise TranscriptionError(f"Audio file not found: {path}")
        else:
            raise TranscriptionError("Invalid source type for transcription")

        if not path or not path.exists():
            raise TranscriptionError("No audio file to transcribe")

        if provider == "openai":
            return _transcribe_openai(path, openai_api_key)
        if provider == "local":
            return _transcribe_whisper_local(path)
        raise TranscriptionError(f"Unknown transcription provider: {provider}")
    finally:
        if cleanup_path and path and path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.debug(f"Could not remove temp file: {e}")


def _transcribe_openai(path: Path, api_key: Optional[str]) -> str:
    """Transcribe using OpenAI Whisper API."""
    if not api_key:
        raise TranscriptionError("OpenAI API key required. Add it in Settings → Integrations.")
    try:
        import httpx
        from openai import OpenAI

        # Use certifi for SSL (fixes connection errors in PyInstaller-frozen macOS app)
        verify = True
        try:
            import certifi
            import sys
            cp = certifi.where()
            if cp and Path(cp).exists():
                verify = cp
            elif getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                bundle_cert = Path(sys._MEIPASS) / "certifi" / "cacert.pem"
                if bundle_cert.exists():
                    verify = str(bundle_cert)
        except Exception:
            pass

        # 60s timeout for audio upload; 3 retries for transient connection errors
        with httpx.Client(verify=verify, timeout=60.0) as http_client:
            client = OpenAI(
                api_key=api_key,
                timeout=60.0,
                max_retries=3,
                http_client=http_client,
            )
            with open(path, "rb") as f:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                )
            return transcript.text if transcript else ""
    except ImportError:
        raise TranscriptionError("openai package required: pip install openai")
    except Exception as e:
        err_msg = str(e).strip()
        if "connection" in err_msg.lower() or "connect" in err_msg.lower():
            hint = (
                "Connection error. Check: (1) Internet connection, (2) Firewall/VPN not blocking api.openai.com, "
                "(3) Try Settings → Integrations → Transcription Provider → local (Whisper on device)."
            )
            raise TranscriptionError(f"OpenAI transcription failed: {err_msg}. {hint}") from e
        logger.error(f"OpenAI transcription failed: {e}", exc_info=True)
        raise TranscriptionError(f"OpenAI transcription failed: {e}") from e


def _transcribe_whisper_local(path: Path) -> str:
    """Transcribe using local Whisper model."""
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(str(path), fp16=False, language="en")
        return result.get("text", "").strip() if result else ""
    except ImportError as e:
        raise TranscriptionError(
            "openai-whisper not found. Run: pip install openai-whisper"
        ) from e
    except Exception as e:
        if "ffmpeg" in str(e).lower():
            raise TranscriptionError(
                "ffmpeg is required for audio transcription. Install with: brew install ffmpeg"
            ) from e
        logger.error(f"Local Whisper transcription failed: {e}", exc_info=True)
        raise TranscriptionError(f"Local Whisper failed: {e}") from e
