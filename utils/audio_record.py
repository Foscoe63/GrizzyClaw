"""Microphone recording for local voice chat"""

import logging
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)

# Whisper prefers 16kHz mono
SAMPLE_RATE = 16000
CHANNELS = 1


def _process_chunks_to_wav(chunks: list, out_path: str, sample_rate: int, channels: int) -> bool:
    """Process chunks in a separate process to avoid blocking the UI (GIL)."""
    import numpy as np
    from scipy.io import wavfile

    if not chunks:
        return False
    try:
        recording = np.concatenate(chunks, axis=0)
        if channels == 1 and len(recording.shape) > 1:
            recording = recording.squeeze()
        recording_int16 = (recording * 32767).astype("int16")
        wavfile.write(out_path, sample_rate, recording_int16)
        return True
    except Exception as e:
        logger.error(f"Failed to process audio chunks: {e}", exc_info=True)
        return False


def is_recording_available() -> bool:
    """Check if microphone recording is available (sounddevice installed)."""
    try:
        import sounddevice as sd
        sd.check_input_settings()
        return True
    except ImportError:
        return False
    except Exception:
        return False


def record_audio(
    duration_sec: float = 10.0,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    device: Optional[int] = None,
) -> Optional[Path]:
    """
    Record audio from the default microphone.

    Args:
        duration_sec: Recording duration in seconds
        sample_rate: Sample rate (16kHz recommended for Whisper)
        channels: Number of channels (1 = mono)

    Returns:
        Path to temporary WAV file, or None on failure
    """
    try:
        import sounddevice as sd
        import numpy as np
        from scipy.io import wavfile
    except ImportError as e:
        logger.warning(f"Recording requires sounddevice and scipy: {e}")
        return None

    try:
        frames = int(duration_sec * sample_rate)
        recording = sd.rec(
            frames,
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            device=device,
        )
        sd.wait()
        # Convert to int16 for WAV
        recording_int16 = (recording * 32767).astype("int16")
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            import os

            os.close(fd)
            wavfile.write(path, sample_rate, recording_int16)
            return Path(path)
        except Exception:
            import os

            try:
                os.close(fd)
            except OSError:
                pass
            Path(path).unlink(missing_ok=True)
            raise
    except Exception as e:
        logger.error(f"Microphone recording failed: {e}", exc_info=True)
        return None


def list_input_devices() -> list[tuple[int, str]]:
    """Return list of (device_index, name) for all input devices."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        result = []
        for i in range(len(devices)):
            d = devices[i]
            max_in = d.get("max_input_channels", 0) if isinstance(d, dict) else getattr(d, "max_input_channels", 0)
            if max_in > 0:
                name = d.get("name", f"Device {i}") if isinstance(d, dict) else getattr(d, "name", f"Device {i}")
                result.append((i, str(name)))
        return result
    except Exception:
        return []


def record_audio_callback(
    stop_event,
    out_path: Path,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    chunk_duration_ms: int = 100,
    device: Optional[Union[int, str]] = None,
) -> bool:
    """
    Record audio until stop_event is set. Runs in a thread.

    Args:
        stop_event: threading.Event or similar; when set, recording stops
        out_path: Path to save WAV file
        sample_rate: Sample rate
        channels: Number of channels
        chunk_duration_ms: Chunk size for streaming

    Returns:
        True if recording succeeded, False otherwise
    """
    try:
        import sounddevice as sd
        import numpy as np
        from scipy.io import wavfile
    except ImportError as e:
        logger.warning(f"Recording requires sounddevice and scipy: {e}")
        return False

    chunks = []
    chunk_frames = int(sample_rate * chunk_duration_ms / 1000) * channels

    def callback(indata, frames, time_info, status):
        if status:
            logger.debug(f"Recording status: {status}")
        chunks.append(indata.copy())

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=chunk_frames,
            callback=callback,
            device=device,
        ):
            while not stop_event.is_set():
                stop_event.wait(timeout=0.1)

        if not chunks:
            return False

        # Process chunks in a separate process to avoid GIL blocking the UI
        try:
            with ProcessPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    _process_chunks_to_wav,
                    chunks,
                    str(out_path),
                    sample_rate,
                    channels,
                )
                return future.result(timeout=120)
        except Exception as e:
            logger.error(f"Process pool failed, falling back to in-thread: {e}")
            # Fallback: process in-thread (may freeze UI for long recordings)
            recording = np.concatenate(chunks, axis=0)
            if channels == 1 and len(recording.shape) > 1:
                recording = recording.squeeze()
            recording_int16 = (recording * 32767).astype("int16")
            wavfile.write(str(out_path), sample_rate, recording_int16)
            return True
    except Exception as e:
        logger.error(f"Streaming recording failed: {e}", exc_info=True)
        return False
