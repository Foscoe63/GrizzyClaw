"""Media pipeline orchestration: ingest, transcribe, route to agent"""

import logging
from pathlib import Path
from typing import Optional, Union

from .transcribe import transcribe_audio

logger = logging.getLogger(__name__)


def process_audio_for_agent(
    source: Union[str, bytes],
    provider: str = "openai",
    openai_api_key: Optional[str] = None,
) -> str:
    """
    Transcribe audio and return text suitable for agent input.

    Args:
        source: File path, bytes, or base64 audio
        provider: "local" or "openai"
        openai_api_key: For OpenAI provider

    Returns:
        Transcribed text; empty string on failure
    """
    return transcribe_audio(
        source=source,
        provider=provider,
        openai_api_key=openai_api_key,
    )
