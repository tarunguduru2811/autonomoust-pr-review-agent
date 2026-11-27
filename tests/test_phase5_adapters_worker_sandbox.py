"""Tests for Phase 5: Gemini adapters, SandboxRunner, ReviewWorker, and RunMetrics."""

import json
from pathlib import Path
from typing import Any

import httpx

from pr_agent.agents.gemini_adapter import GeminiAgentLLM, GeminiTriageLLM
from pr_agent.agents.runner import LLMTurnResponse
from pr_agent.observability import InMemoryMetricsSink
from pr_agent.orchestrator.pipeline import ReviewPipeline
from pr_agent.orchestrator.router import TwoTierRouter
from pr_agent.orchestrator.worker import ReviewWorker
from pr_agent.queue import InMemoryJobQueue, JobStatus, ReviewJob
from pr_agent.sandbox import SandboxRunner
from pr_agent.tools.schemas import READ_ONLY_TOOL_SCHEMAS

SAMPLE_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,1 +1,2 @@
 def hello():
+    return 1
"""


async def test_gemini_triage_and_agent_adapters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        if "triage" in body["systemInstruction"]["parts"][0]["text"].lower():
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "skip_review": False,
                                                "invoke": ["logic"],
                                                "reason": "Python logic change",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                },
            )

        # Agent call with tools -> return a functionCall on first call
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "read_file",
                                        "args": {"path": "app.py", "start_line": 1},
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        triage = GeminiTriageLLM(api_key="test-key", http_client=client)
        triage_json = await triage.classify("Fix", "Desc", ["app.py"])
        assert '"invoke": ["logic"]' in triage_json

        agent = GeminiAgentLLM(api_key="test-key", http_client=client)
        turn = await agent.step(
            "You are logic agent",
            [{"role": "user", "content": "Review"}],
            tools=READ_ONLY_TOOL_SCHEMAS,
        )
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].tool == "read_file"
        assert turn.tool_calls[0].args["path"] == "app.py"


def test_sandbox_runner_builds_hardened_command(tmp_path: Path) -> None:
    runner = SandboxRunner(memory_limit="256m", cpu_limit="0.5")
    cmd = runner.build_docker_command(tmp_path, ["pytest", "-q"])

    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges:true" in cmd
    assert "--memory=256m" in cmd
    assert f"{tmp_path.resolve()}:/workspace:ro" in cmd
    # Ensure no host secrets are passed via -e / --env
    assert "-e" not in cmd
    assert "--env" not in cmd


class _FakeGitHubClient:
    def __init__(self) -> None:
        self.check_runs: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []

    async def get_pull_request(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> dict[str, Any]:
        return {"title": "Add hello", "body": "Returns 1", "head": {"sha": "sha_worker"}}

    async def get_pull_request_diff(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> str:
        return SAMPLE_DIFF

    async def create_check_run(self, **kwargs: Any) -> dict[str, Any]:
        self.check_runs.append(kwargs)
        return {"id": 1}

    async def create_pull_request_review(self, **kwargs: Any) -> dict[str, Any]:
        self.reviews.append(kwargs)
        return {"id": 2}


class _FakeWorkspaceProvider:
    def __init__(self) -> None:
        self.checked_out_dir: Path | None = None

    async def checkout(self, job: ReviewJob, target_dir: Path) -> None:
        self.checked_out_dir = target_dir
        (target_dir / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")


class _CleanLLM:
    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMTurnResponse:
        return LLMTurnResponse(final_json='{"findings": []}')


async def test_review_worker_processes_job_cleans_temp_and_emits_metrics() -> None:
    queue = InMemoryJobQueue()
    job = ReviewJob(
        repo_id=1,
        repo_full_name="acme/app",
        pr_number=12,
        head_sha="sha_worker",
        installation_id=7,
        trigger_event="pull_request.opened",
    )
    await queue.enqueue(job)

    gh = _FakeGitHubClient()
    metrics = InMemoryMetricsSink()
    pipeline = ReviewPipeline(
        github_client=gh,  # type: ignore[arg-type]
        router=TwoTierRouter(),
        specialist_llm=_CleanLLM(),
        metrics_sink=metrics,
    )
    workspace_provider = _FakeWorkspaceProvider()
    worker = ReviewWorker(
        job_queue=queue,
        github_client=gh,  # type: ignore[arg-type]
        pipeline=pipeline,
        workspace_provider=workspace_provider,
    )

    processed = await worker.process_next()
    assert processed is True
    assert await worker.process_next() is False

    stored_job = queue.get_job(job.job_key)
    assert stored_job is not None and stored_job.status == JobStatus.COMPLETED

    # Temp workspace cleaned up after execution
    assert workspace_provider.checked_out_dir is not None
    assert not workspace_provider.checked_out_dir.exists()

    # Metrics emitted
    assert len(metrics.records) == 1
    assert metrics.records[0].status == "completed"
    assert metrics.records[0].pr_number == 12
