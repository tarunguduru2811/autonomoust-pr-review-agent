"""Strict Pydantic schemas for specialist and Judge outputs."""

from typing import Literal

from pydantic import BaseModel, Field

from pr_agent.orchestrator.run_context import CandidateFinding


class SpecialistOutput(BaseModel):
    """Structured output returned by a specialist subagent."""

    findings: list[CandidateFinding] = Field(default_factory=list)


class FinalReviewComment(BaseModel):
    """Verified comment approved by the Judge for GitHub publishing."""

    file: str
    line: int
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    severity: Literal["critical", "high", "medium", "low"]
    category: str
    title: str
    body: str
    suggested_fix: str | None = None
    fingerprint: str


class JudgeOutput(BaseModel):
    """Final synthesized review produced by the single Judge."""

    summary: str
    comments: list[FinalReviewComment] = Field(default_factory=list)
    dropped_count: int = 0

