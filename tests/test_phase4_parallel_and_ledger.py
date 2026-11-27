"""Tests for Phase 4: parallel specialists, tool schemas, and PR-lifecycle deduplication."""

import json
from pathlib import Path
from typing import Any

from pr_agent.agents.runner import LLMTurnResponse, compute_fingerprint
from pr_agent.db import CommentStatus, InMemoryPRLedger
from pr_agent.orchestrator.pipeline import ReviewPipeline
from pr_agent.orchestrator.router import TwoTierRouter
from pr_agent.queue.models import ReviewJob
from pr_agent.tools.schemas import READ_ONLY_TOOL_SCHEMAS

AUTH_AND_DB_DIFF = """diff --git a/src/auth/orders.py b/src/auth/orders.py
--- a/src/auth/orders.py
+++ b/src/auth/orders.py
@@ -1,2 +1,4 @@
 def refund(order):
+    db.execute("UPDATE orders SET status='refunded'")
+    return True
     pass
diff --git a/db/migrations/002.sql b/db/migrations/002.sql
--- a/db/migrations/002.sql
+++ b/db/migrations/002.sql
@@ -1,1 +1,2 @@
+ALTER TABLE orders ADD COLUMN note TEXT;
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


class _ParallelSpecialistLLM:
    def __init__(self, commit_stage: int = 1) -> None:
        self.commit_stage = commit_stage
        self.invoked_prompts: list[str] = []

    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        assert tools is not None and len(tools) == 3
        self.invoked_prompts.append(system_prompt[:40])

        if "Security Specialist" in system_prompt:
            return LLMTurnResponse(
                final_json=json.dumps(
                    {
                        "findings": [
                            {
                                "agent_name": "security",
                                "file": "src/auth/orders.py",
                                "line": 2,
                                "severity": "critical",
                                "confidence": 0.95,
                                "category": "idor",
                                "title": "Missing ownership check",
                                "explanation": "Order owner not verified.",
                            }
                        ]
                    }
                )
            )

        if "Database & Test Specialist" in system_prompt:
            if self.commit_stage == 1:
                raise TimeoutError("DB agent timed out")
            return LLMTurnResponse(
                final_json=json.dumps(
                    {
                        "findings": [
                            {
                                "agent_name": "db",
                                "file": "db/migrations/002.sql",
                                "line": 1,
                                "severity": "medium",
                                "confidence": 0.88,
                                "category": "migration",
                                "title": "Add index on orders",
                                "explanation": "New column queried without index.",
                            }
                        ]
                    }
                )
            )

        # Logic specialist
        if self.commit_stage == 1:
            return LLMTurnResponse(
                final_json=json.dumps(
                    {
                        "findings": [
                            {
                                "agent_name": "logic",
                                "file": "src/auth/orders.py",
                                "line": 3,
                                "severity": "high",
                                "confidence": 0.9,
                                "category": "state",
                                "title": "Double refund possible",
                                "explanation": "No check for already refunded status.",
                            }
                        ]
                    }
                )
            )
        return LLMTurnResponse(final_json=json.dumps({"findings": []}))


def test_read_only_tool_schemas_defined() -> None:
    names = [t["name"] for t in READ_ONLY_TOOL_SCHEMAS]
    assert names == ["read_file", "search_code", "get_symbol_definition"]


async def test_parallel_specialists_and_two_commit_deduplication(tmp_path: Path) -> None:
    gh = _FakeGitHubClient()
    ledger = InMemoryPRLedger()
    router = TwoTierRouter()

    # Commit 1: logic + security succeed, db times out (partial resilience).
    job1 = ReviewJob(
        repo_id=10,
        repo_full_name="acme/app",
        pr_number=55,
        head_sha="sha_1",
        installation_id=1,
        trigger_event="pull_request.opened",
    )
    pipeline1 = ReviewPipeline(
        github_client=gh,  # type: ignore[arg-type]
        router=router,
        specialist_llm=_ParallelSpecialistLLM(commit_stage=1),
        pr_ledger=ledger,
    )
    res1 = await pipeline1.execute(job1, tmp_path, AUTH_AND_DB_DIFF)
    assert res1 is not None
    assert len(res1.comments) == 2

    # Developer resolves the IDOR security comment and fixes the double-refund bug.
    idor_fp = compute_fingerprint("src/auth/orders.py", "idor", "Missing ownership check")
    double_refund_fp = compute_fingerprint("src/auth/orders.py", "state", "Double refund possible")
    await ledger.update_comment_status(10, 55, idor_fp, CommentStatus.RESOLVED)

    # Commit 2: Security flags IDOR again -> suppressed; Logic clean -> marked FIXED; DB flags new.
    job2 = ReviewJob(
        repo_id=10,
        repo_full_name="acme/app",
        pr_number=55,
        head_sha="sha_2",
        installation_id=1,
        trigger_event="pull_request.synchronize",
    )
    pipeline2 = ReviewPipeline(
        github_client=gh,  # type: ignore[arg-type]
        router=router,
        specialist_llm=_ParallelSpecialistLLM(commit_stage=2),
        pr_ledger=ledger,
    )
    res2 = await pipeline2.execute(job2, tmp_path, AUTH_AND_DB_DIFF)
    assert res2 is not None
    assert len(res2.comments) == 1
    assert res2.comments[0].title == "Add index on orders"
    assert res2.dropped_count == 1  # IDOR suppressed

    threads = {t["fingerprint"]: t["status"] for t in await ledger.get_existing_threads(10, 55)}
    assert threads[idor_fp] == "resolved"
    assert threads[double_refund_fp] == "fixed"
