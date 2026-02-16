"""PII detection and redaction for logs."""

import re
from typing import Optional

PII_REDACTED = "[REDACTED]"

# Common PII patterns
_PII_PATTERNS = [
    # Email
    (r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "email"),
    # US SSN (XXX-XX-XXXX)
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),
    # US phone (various formats)
    (r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b", "phone"),
    (r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}\b", "phone"),
    # Credit card (simplified - 4 groups of 4 digits)
    (r"\b(?:\d{4}[-.\s]?){3}\d{4}\b", "card"),
    # API keys (common prefixes)
    (r"\b(sk-[a-zA-Z0-9]{20,})\b", "api_key"),
    (r"\b(ghp_[a-zA-Z0-9]{36})\b", "api_key"),
    (r"\b(gho_[a-zA-Z0-9]{36})\b", "api_key"),
]


def redact_pii(text: str, replacement: str = PII_REDACTED) -> str:
    """
    Redact PII from text. Returns text with matches replaced.
    """
    if not text:
        return text
    result = text
    for pattern, _ in _PII_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result


def redact_pii_for_log(msg: str, *args, **kwargs) -> tuple:
    """
    Redact PII from log message and args. Use as:
      logger.info(redact_pii_for_log("User %s logged in", user_email))
    Returns (redacted_msg, redacted_args, redacted_kwargs) for logging.
    """
    redacted_msg = redact_pii(str(msg))
    redacted_args = tuple(redact_pii(str(a)) for a in args)
    redacted_kwargs = {k: redact_pii(str(v)) for k, v in kwargs.items()}
    return (redacted_msg,) + redacted_args if args else (redacted_msg,), redacted_kwargs
