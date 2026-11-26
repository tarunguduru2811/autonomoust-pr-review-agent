"""Scoped shared RunContext for parallel subagents and the Judge."""

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field


class CandidateFinding(BaseModel):
    """Structured finding produced by a specialist subagent."""

    agent_name: str
    file: str
    line: int
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    severity: Literal["critical", "high", "medium", "low"]
    confidence: float = Field(ge=0.0, le=1.0)
    category: str
    title: str
    explanation: str
    suggested_fix: str | None = None


class PRSnapshot(BaseModel):
    """Immutable PR context created once by the Orchestrator before spawning subagents."""

    repo_full_name: str
    pr_number: int
    head_sha: str
    title: str = ""
    description: str = ""
    cleaned_diff: str
    changed_files: list[str] = Field(default_factory=list)
    repo_rules: str = ""
    static_findings: list[dict[str, Any]] = Field(default_factory=list)
    existing_threads: list[dict[str, Any]] = Field(default_factory=list)


class RunContext:
    """Shared per-run memory: immutable snapshot, tool-result cache, and findings blackboard."""

    def __init__(self, run_id: str, pr_snapshot: PRSnapshot) -> None:
        self.run_id = run_id
        self.pr_snapshot = pr_snapshot
        self._tool_cache: dict[str, Any] = {}
        self._findings_blackboard: list[CandidateFinding] = []
        self._lock = asyncio.Lock()

    def get_cached_tool_result(self, cache_key: str) -> tuple[bool, Any]:
        """Look up a previously cached tool output."""
        if cache_key in self._tool_cache:
            return True, self._tool_cache[cache_key]
        return False, None

    async def set_cached_tool_result(self, cache_key: str, value: Any) -> None:
        """Store a tool output in the shared run cache."""
        async with self._lock:
            self._tool_cache[cache_key] = value

    async def add_findings(self, findings: list[CandidateFinding]) -> None:
        """Append structured findings from a specialist to the shared blackboard."""
        async with self._lock:
            self._findings_blackboard.extend(findings)

    def get_findings(self) -> list[CandidateFinding]:
        """Return a copy of all candidate findings collected so far."""
        return list(self._findings_blackboard)

