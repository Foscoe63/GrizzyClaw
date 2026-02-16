"""Logging filter that redacts PII from log records."""

import logging

from grizzyclaw.safety.pii import redact_pii


class PIIRedactionFilter(logging.Filter):
    """Filter that redacts PII from log messages before they are emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_pii(str(record.msg))
        if record.args:
            record.args = tuple(redact_pii(str(a)) for a in record.args)
        return True
