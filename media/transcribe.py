"""Audio transcription via Whisper (local) or OpenAI API"""

import base64
import logging
import tempfile
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


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
                logger.warning(f"Audio file not found: {path}")
                return ""
        else:
            logger.warning("Invalid source type for transcription")
            return ""

        if not path or not path.exists():
            return ""

        if provider == "openai":
            return _transcribe_openai(path, openai_api_key)
        if provider == "local":
            return _transcribe_whisper_local(path)
        logger.warning(f"Unknown transcription provider: {provider}")
        return ""
    finally:
        if cleanup_path and path and path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.debug(f"Could not remove temp file: {e}")


def _transcribe_openai(path: Path, api_key: Optional[str]) -> str:
    """Transcribe using OpenAI Whisper API."""
    if not api_key:
        logger.warning("OpenAI API key required for transcription")
        return ""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        with open(path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return transcript.text if transcript else ""
    except ImportError:
        logger.warning("openai package required for transcription")
        return ""
    except Exception as e:
        logger.error(f"OpenAI transcription failed: {e}", exc_info=True)
        return ""


def _transcribe_whisper_local(path: Path) -> str:
    """Transcribe using local Whisper model."""
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(str(path), fp16=False)
        return result.get("text", "").strip() if result else ""
    except ImportError:
        logger.warning("openai-whisper package required for local transcription")
        return ""
    except Exception as e:
        logger.error(f"Local Whisper transcription failed: {e}", exc_info=True)
        return ""
