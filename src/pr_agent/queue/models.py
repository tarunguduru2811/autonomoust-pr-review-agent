"""Data models for queued PR review jobs."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """Lifecycle states for a PR review job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ReviewJob(BaseModel):
    """Represents a single PR review request keyed by (repo_id, pr_number, head_sha)."""

    repo_id: int
    repo_full_name: str
    pr_number: int
    head_sha: str
    installation_id: int
    trigger_event: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: JobStatus = JobStatus.QUEUED

    @property
    def job_key(self) -> str:
        """Unique idempotency key for this commit on this PR."""
        return f"{self.repo_id}:{self.pr_number}:{self.head_sha}"

    @property
    def pr_key(self) -> str:
        """Key identifying the PR across commits."""
        return f"{self.repo_id}:{self.pr_number}"

