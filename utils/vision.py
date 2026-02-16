"""Vision utilities for loading and encoding images."""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _detect_media_type(path: str) -> str:
    """Detect media type from file extension."""
    ext = Path(path).suffix.lower()
    if ext in (".png",):
        return "image/png"
    if ext in (".jpg", ".jpeg",):
        return "image/jpeg"
    if ext in (".gif",):
        return "image/gif"
    if ext in (".webp",):
        return "image/webp"
    return "image/png"


def load_image_to_base64(
    source: str,
) -> Optional[Tuple[str, str]]:
    """
    Load image from path or data URL to base64.
    Returns (base64_data, media_type) or None on failure.
    """
    if source.startswith("data:image"):
        # Data URL: data:image/png;base64,...
        try:
            header, rest = source.split(",", 1)
            media_type = "image/png"
            if ";" in header:
                mt = header.split(";")[0].replace("data:", "")
                if mt:
                    media_type = mt
            return (rest, media_type)
        except Exception as e:
            logger.debug(f"Failed to parse data URL: {e}")
            return None

    # File path
    path = Path(source).expanduser()
    if not path.exists():
        logger.warning(f"Image not found: {path}")
        return None
    try:
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        media_type = _detect_media_type(str(path))
        return (b64, media_type)
    except Exception as e:
        logger.warning(f"Failed to load image {path}: {e}")
        return None


def build_vision_content(
    text: str,
    image_sources: List[str],
) -> Tuple[str, List[dict]]:
    """
    Build multipart content for vision-capable models.
    Returns (text_for_session, content_blocks) where content_blocks is
    OpenAI-format: [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "data:..."}}]
    """
    blocks: List[dict] = []
    if text.strip():
        blocks.append({"type": "text", "text": text.strip()})

    for src in image_sources:
        result = load_image_to_base64(src)
        if result:
            b64, media_type = result
            data_url = f"data:{media_type};base64,{b64}"
            blocks.append({
                "type": "image_url",
                "image_url": {"url": data_url, "detail": "auto"},
            })

    if not blocks:
        blocks = [{"type": "text", "text": "(no content)"}]

    # For session storage, use text only
    text_for_session = text or "(image)"

    return text_for_session, blocks
