"""Structured per-run telemetry and metrics sinks."""

import logging
from typing import Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger("pr_agent.telemetry")


class RunMetrics(BaseModel):
    """Structured telemetry recorded for each PR review run."""

    run_id: str
    repo_full_name: str
    pr_number: int
    head_sha: str
    specialists_invoked: list[str] = Field(default_factory=list)
    findings_raw: int = 0
    findings_published: int = 0
    findings_dropped: int = 0
    duration_ms: float = 0.0
    status: str = "completed"


class MetricsSink(Protocol):
    """Interface for exporting PR review run metrics."""

    async def record(self, metrics: RunMetrics) -> None: ...


class InMemoryMetricsSink:
    """Stores metrics in memory for testing and inspection."""

    def __init__(self) -> None:
        self.records: list[RunMetrics] = []

    async def record(self, metrics: RunMetrics) -> None:
        self.records.append(metrics)


class LoggingMetricsSink:
    """Emits structured JSON metrics to standard logging / OTel collectors."""

    async def record(self, metrics: RunMetrics) -> None:
        logger.info("pr_review_metrics %s", metrics.model_dump_json())
