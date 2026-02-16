"""Simple in-memory metrics for latency, token counts, error rates."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MetricPoint:
    """Single metric value with timestamp."""
    value: float
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Collector for LLM and agent metrics."""

    def __init__(self):
        self._llm_latencies: List[float] = []
        self._llm_tokens_in: List[int] = []
        self._llm_tokens_out: List[int] = []
        self._llm_errors: int = 0
        self._llm_calls: int = 0
        self._agent_requests: int = 0
        self._max_samples = 1000

    def record_llm_call(
        self,
        latency_sec: float,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error: bool = False,
    ) -> None:
        self._llm_calls += 1
        if error:
            self._llm_errors += 1
        else:
            self._llm_latencies.append(latency_sec)
            self._llm_tokens_in.append(tokens_in)
            self._llm_tokens_out.append(tokens_out)
            if len(self._llm_latencies) > self._max_samples:
                self._llm_latencies.pop(0)
                self._llm_tokens_in.pop(0)
                self._llm_tokens_out.pop(0)

    def record_agent_request(self) -> None:
        self._agent_requests += 1

    def get_stats(self) -> Dict:
        """Return current metrics snapshot."""
        latencies = self._llm_latencies[-100:] if self._llm_latencies else []
        return {
            "llm": {
                "calls": self._llm_calls,
                "errors": self._llm_errors,
                "error_rate": self._llm_errors / self._llm_calls if self._llm_calls else 0,
                "latency_mean_sec": sum(latencies) / len(latencies) if latencies else 0,
                "latency_p99_sec": sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 10 else 0,
                "tokens_in_total": sum(self._llm_tokens_in),
                "tokens_out_total": sum(self._llm_tokens_out),
            },
            "agent": {
                "requests": self._agent_requests,
            },
        }

    def reset(self) -> None:
        self._llm_latencies.clear()
        self._llm_tokens_in.clear()
        self._llm_tokens_out.clear()
        self._llm_errors = 0
        self._llm_calls = 0
        self._agent_requests = 0


_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
