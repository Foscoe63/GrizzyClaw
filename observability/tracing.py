"""Optional OpenTelemetry tracing. No-op when opentelemetry not installed."""

import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)

_tracer = None
_otel_available = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    _otel_available = True
except ImportError:
    pass


def init_tracing(
    service_name: str = "grizzyclaw",
    export_console: bool = False,
) -> bool:
    """Initialize OpenTelemetry tracing. Returns True if initialized."""
    global _tracer
    if not _otel_available:
        logger.debug("OpenTelemetry not installed, tracing disabled")
        return False
    try:
        provider = TracerProvider()
        if export_console:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        from grizzyclaw import __version__

        _tracer = trace.get_tracer(service_name, __version__)
        logger.info("OpenTelemetry tracing initialized")
        return True
    except Exception as e:
        logger.warning(f"Failed to init tracing: {e}")
        return False


@contextmanager
def span(name: str, attributes: Optional[Dict[str, Any]] = None) -> Generator[Any, None, None]:
    """Context manager for a traced span. No-op when tracing disabled."""
    if _tracer and _otel_available:
        with _tracer.start_as_current_span(name) as s:
            if attributes:
                for k, v in attributes.items():
                    s.set_attribute(k, str(v))
            yield s
    else:
        yield None


def trace_llm_call(provider: str, model: str):
    """Decorator/context for tracing LLM calls."""
    return span("llm.generate", {"llm.provider": provider, "llm.model": model})
