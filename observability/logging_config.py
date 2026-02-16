"""Structured JSON logging configuration."""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Optional


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        if record.pathname:
            log_obj["module"] = record.module
            log_obj["lineno"] = record.lineno
        if self.include_extra and hasattr(record, "extra") and record.extra:
            log_obj["extra"] = record.extra
        return json.dumps(log_obj, default=str)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    pii_redact: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """
    Configure application logging.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)

    if json_format:
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    if pii_redact:
        from grizzyclaw.utils.pii_filter import PIIRedactionFilter
        handler.addFilter(PIIRedactionFilter())
    root.addHandler(handler)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        if pii_redact:
            fh.addFilter(PIIRedactionFilter())
        root.addHandler(fh)
