"""Data models for PR-lifecycle memory in PostgreSQL."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CommentStatus(StrEnum):
    """Lifecycle states for a posted bot review comment."""

    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    FIXED = "fixed"


class PRReviewRecord(BaseModel):
    """Tracks the latest reviewed commit for a PR."""

    repo_id: int
    pr_number: int
    last_reviewed_sha: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReviewCommentRecord(BaseModel):
    """Tracks a fingerprinted bot comment across commits on a PR."""

    repo_id: int
    pr_number: int
    head_sha: str
    fingerprint: str
    file: str
    line: int
    category: str
    title: str
    comment_id: int | None = None
    status: CommentStatus = CommentStatus.OPEN
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
