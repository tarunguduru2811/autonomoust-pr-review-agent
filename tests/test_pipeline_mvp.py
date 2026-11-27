"""Integration tests for the Phase 3 MVP ReviewPipeline."""

import json
from pathlib import Path
from typing import Any

from pr_agent.agents.runner import LLMTurnResponse, ToolCallRequest
from pr_agent.github.client import CheckRunConclusion, CheckRunStatus
from pr_agent.orchestrator.pipeline import ReviewPipeline
from pr_agent.orchestrator.router import TwoTierRouter
from pr_agent.queue.models import ReviewJob

SAMPLE_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,2 +1,4 @@
 def process(order):
+    if order.total < 0:
+        return None
     return order.total
"""


class _FakeGitHubClient:
    def __init__(self) -> None:
        self.check_runs: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []

    async def create_check_run(self, **kwargs: Any) -> dict[str, Any]:
        self.check_runs.append(kwargs)
        return {"id": 1}

    async def create_pull_request_review(self, **kwargs: Any) -> dict[str, Any]:
        self.reviews.append(kwargs)
        return {"id": 2}


class _ToolThenFindingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMTurnResponse(
                tool_calls=[
                    ToolCallRequest(tool="read_file", args={"path": "app.py", "start_line": 1})
                ]
            )
        return LLMTurnResponse(
            final_json=json.dumps(
                {
                    "findings": [
                        {
                            "agent_name": "logic",
                            "file": "app.py",
                            "line": 2,
                            "side": "RIGHT",
                            "severity": "high",
                            "confidence": 0.92,
                            "category": "validation",
                            "title": "Negative total returns None silently",
                            "explanation": "Callers expect a number or raised ValueError.",
                            "suggested_fix": "raise ValueError('negative total')",
                        },
                        {
                            "agent_name": "logic",
                            "file": "app.py",
                            "line": 99,  # Outside diff hunk -> summary fallback
                            "side": "RIGHT",
                            "severity": "medium",
                            "confidence": 0.85,
                            "category": "contract",
                            "title": "Caller mismatch",
                            "explanation": "Check caller at line 99.",
                        },
                        {
                            "agent_name": "logic",
                            "file": "app.py",
                            "line": 3,
                            "side": "RIGHT",
                            "severity": "low",
                            "confidence": 0.3,  # Filtered out by Judge (<0.6)
                            "category": "nitpick",
                            "title": "Minor style",
                            "explanation": "Nitpick.",
                        },
                    ]
                }
            )
        )


class _FailingLLM:
    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        raise TimeoutError("LLM timed out")


def _make_job() -> ReviewJob:
    return ReviewJob(
        repo_id=1,
        repo_full_name="acme/backend",
        pr_number=7,
        head_sha="sha_mvp",
        installation_id=99,
        trigger_event="pull_request.opened",
    )


async def test_mvp_pipeline_runs_tools_filters_and_maps_lines(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def process(order):\n    return order.total\n",
        encoding="utf-8",
    )
    gh = _FakeGitHubClient()
    pipeline = ReviewPipeline(
        github_client=gh,  # type: ignore[arg-type]
        router=TwoTierRouter(),
        specialist_llm=_ToolThenFindingLLM(),
    )

    result = await pipeline.execute(_make_job(), tmp_path, SAMPLE_DIFF, pr_title="Validate total")
    assert result is not None
    assert len(result.comments) == 2
    assert result.dropped_count == 1

    assert len(gh.reviews) == 1
    # Only line 2 is in diff hunk; line 99 falls back to review body.
    assert len(gh.reviews[0]["comments"]) == 1
    assert gh.reviews[0]["comments"][0]["line"] == 2
    assert "Additional Findings (Outside Diff Hunk)" in gh.reviews[0]["body"]

    assert gh.check_runs[-1]["status"] == CheckRunStatus.COMPLETED
    assert gh.check_runs[-1]["conclusion"] == CheckRunConclusion.ACTION_REQUIRED


async def test_mvp_pipeline_fails_open_on_llm_timeout(tmp_path: Path) -> None:
    gh = _FakeGitHubClient()
    pipeline = ReviewPipeline(
        github_client=gh,  # type: ignore[arg-type]
        router=TwoTierRouter(),
        specialist_llm=_FailingLLM(),
    )

    result = await pipeline.execute(_make_job(), tmp_path, SAMPLE_DIFF)
    assert result is None
    assert gh.reviews == []
    assert gh.check_runs[-1]["status"] == CheckRunStatus.COMPLETED
    assert gh.check_runs[-1]["conclusion"] == CheckRunConclusion.NEUTRAL
