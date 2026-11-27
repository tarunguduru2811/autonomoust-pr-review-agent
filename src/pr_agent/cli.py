"""CLI entrypoints for live local PR benchmarking and running the webhook server + worker."""

import argparse
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import uvicorn

from pr_agent.agents.gemini_adapter import GeminiAgentLLM, GeminiTriageLLM
from pr_agent.config import Settings, get_settings
from pr_agent.github.auth import GitHubAppAuth
from pr_agent.github.client import GitHubClient
from pr_agent.observability import InMemoryMetricsSink, LoggingMetricsSink
from pr_agent.orchestrator.pipeline import ReviewPipeline
from pr_agent.orchestrator.router import TwoTierRouter
from pr_agent.orchestrator.worker import GitWorkspaceProvider, ReviewWorker
from pr_agent.queue import ReviewJob, create_job_queue
from pr_agent.webhook import create_app


class DryRunGitHubClient:
    """Captures Check Runs and PR Reviews locally instead of calling GitHub."""

    def __init__(self) -> None:
        self.check_runs: list[dict[str, Any]] = []
        self.reviews: list[dict[str, Any]] = []

    async def create_check_run(self, **kwargs: Any) -> dict[str, Any]:
        self.check_runs.append(kwargs)
        return {"id": len(self.check_runs)}

    async def create_pull_request_review(self, **kwargs: Any) -> dict[str, Any]:
        self.reviews.append(kwargs)
        return {"id": len(self.reviews)}


def _get_local_git_diff(repo_path: Path, base: str, head: str) -> tuple[str, str]:
    diff_proc = subprocess.run(
        ["git", "diff", f"{base}..{head}"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    )
    sha_proc = subprocess.run(
        ["git", "rev-parse", head],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    )
    return diff_proc.stdout, sha_proc.stdout.strip()


async def run_local_review(
    repo_path: Path,
    base: str,
    head: str,
    title: str,
    settings: Settings,
) -> dict[str, Any]:
    """Run the full ReviewPipeline against a local git diff using live Gemini models."""
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY is required to run a live review.")

    raw_diff, head_sha = _get_local_git_diff(repo_path, base, head)
    if not raw_diff.strip():
        return {"status": "empty_diff", "findings": [], "metrics": None}

    dry_gh = DryRunGitHubClient()
    metrics_sink = InMemoryMetricsSink()
    triage_llm = GeminiTriageLLM(
        api_key=settings.gemini_api_key,
        model=settings.gemini_triage_model,
    )
    agent_llm = GeminiAgentLLM(
        api_key=settings.gemini_api_key,
        model=settings.gemini_review_model,
    )
    pipeline = ReviewPipeline(
        github_client=dry_gh,  # type: ignore[arg-type]
        router=TwoTierRouter(triage_llm=triage_llm),
        specialist_llm=agent_llm,
        judge_llm=agent_llm,
        metrics_sink=metrics_sink,
    )
    job = ReviewJob(
        repo_id=0,
        repo_full_name=repo_path.resolve().name,
        pr_number=0,
        head_sha=head_sha,
        installation_id=0,
        trigger_event="local.cli",
    )
    judge_out = await pipeline.execute(
        job=job,
        workspace_root=repo_path.resolve(),
        raw_diff=raw_diff,
        pr_title=title,
    )
    return {
        "head_sha": head_sha,
        "summary": judge_out.summary if judge_out else "Skipped or failed open",
        "comments": [c.model_dump() for c in judge_out.comments] if judge_out else [],
        "metrics": metrics_sink.records[0].model_dump() if metrics_sink.records else None,
        "github_reviews_payload": dry_gh.reviews,
    }


async def _serve(host: str, port: int, settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    queue = create_job_queue(settings.queue_backend, settings.redis_url)
    app = create_app(settings=settings, job_queue=queue)

    auth = GitHubAppAuth(
        app_id=settings.github_app_id,
        private_key_pem=settings.resolve_private_key(),
        api_base_url=settings.github_api_base_url,
    )
    gh_client = GitHubClient(auth=auth, api_base_url=settings.github_api_base_url)
    triage_llm = GeminiTriageLLM(
        api_key=settings.gemini_api_key,
        model=settings.gemini_triage_model,
    )
    agent_llm = GeminiAgentLLM(
        api_key=settings.gemini_api_key,
        model=settings.gemini_review_model,
    )
    pipeline = ReviewPipeline(
        github_client=gh_client,
        router=TwoTierRouter(triage_llm=triage_llm),
        specialist_llm=agent_llm,
        judge_llm=agent_llm,
        metrics_sink=LoggingMetricsSink(),
    )
    worker = ReviewWorker(
        job_queue=queue,
        github_client=gh_client,
        pipeline=pipeline,
        workspace_provider=GitWorkspaceProvider(auth),
    )

    async def worker_loop() -> None:
        while True:
            try:
                did_work = await worker.process_next()
                if not did_work:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception:
                logging.getLogger(__name__).exception("Worker loop error")
                await asyncio.sleep(1.0)

    worker_task = asyncio.create_task(worker_loop())
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)


def main() -> None:
    """CLI entrypoint for pr-agent."""
    parser = argparse.ArgumentParser(prog="pr-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    local_p = sub.add_parser("review-local", help="Review a local git diff and print metrics")
    local_p.add_argument("--repo", type=Path, default=Path("."))
    local_p.add_argument("--base", default="HEAD~1")
    local_p.add_argument("--head", default="HEAD")
    local_p.add_argument("--title", default="Local PR Review")

    serve_p = sub.add_parser("serve", help="Run the webhook server and background worker")
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "review-local":
        result = asyncio.run(
            run_local_review(args.repo, args.base, args.head, args.title, settings)
        )
        print(json.dumps(result, indent=2))
    elif args.command == "serve":
        asyncio.run(_serve(args.host, args.port, settings))


if __name__ == "__main__":
    main()
