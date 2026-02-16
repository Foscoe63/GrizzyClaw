"""Content filtering for harmful output."""

import re
from typing import Optional, Tuple

# Blocklist patterns (case-insensitive) for harmful content
# Extend per deployment; kept minimal for default
_HARMFUL_PATTERNS = (
    r"\b(how\s+to\s+)?(make|build|create)\s+(a\s+)?(bomb|explosive|weapon)\b",
    r"\b(how\s+to\s+)?(kill|murder|harm)\s+(yourself|myself|someone)\b",
    r"\b(child\s+)?(porn|sexual\s+abuse)\b",
    r"\b(hack|steal)\s+(into|from)\s+",
    r"\b(illegal\s+)?(drug\s+)?(manufacturing|synthesis)\b",
)


class ContentFilter:
    """Filter harmful content from LLM output."""

    def __init__(self, custom_patterns: Optional[list[str]] = None):
        patterns = list(_HARMFUL_PATTERNS)
        if custom_patterns:
            patterns.extend(custom_patterns)
        self._compiled = [re.compile(p, re.I) for p in patterns]

    def filter(self, text: str) -> Tuple[str, bool]:
        """
        Check text for harmful content.
        Returns (filtered_text, was_filtered).
        When filtered, replaces matched spans with [content blocked].
        """
        if not text:
            return text, False
        filtered = text
        was_filtered = False
        for pat in self._compiled:
            for m in pat.finditer(filtered):
                was_filtered = True
                filtered = filtered[: m.start()] + "[content blocked]" + filtered[m.end() :]
        return filtered, was_filtered

    def is_safe(self, text: str) -> bool:
        """Return True if no harmful content detected."""
        _, blocked = self.filter(text)
        return not blocked


def filter_harmful_content(
    text: str,
    custom_patterns: Optional[list[str]] = None,
) -> Tuple[str, bool]:
    """
    Convenience: filter text with default or custom patterns.
    Returns (filtered_text, was_filtered).
    """
    cf = ContentFilter(custom_patterns=custom_patterns)
    return cf.filter(text)
