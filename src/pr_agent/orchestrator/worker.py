"""Background ReviewWorker that dequeues jobs, prepares a workspace, and runs ReviewPipeline."""

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from pr_agent.github.auth import GitHubAppAuth
from pr_agent.github.client import GitHubClient
from pr_agent.orchestrator.pipeline import ReviewPipeline
from pr_agent.queue import JobQueue, JobStatus, ReviewJob


class WorkspaceProvider(Protocol):
    """Prepares a local directory containing the PR's head commit."""

    async def checkout(self, job: ReviewJob, target_dir: Path) -> None: ...


class GitWorkspaceProvider:
    """Shallow-clones a GitHub repo at the PR's head SHA using an installation token."""

    def __init__(self, auth: GitHubAppAuth) -> None:
        self._auth = auth

    async def checkout(self, job: ReviewJob, target_dir: Path) -> None:
        token = await self._auth.get_installation_token(job.installation_id)
        clone_url = f"https://x-access-token:{token}@github.com/{job.repo_full_name}.git"

        init_proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await init_proc.communicate()

        fetch_proc = await asyncio.create_subprocess_exec(
            "git",
            "fetch",
            "--depth=1",
            clone_url,
            job.head_sha,
            cwd=str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(fetch_proc.communicate(), timeout=60.0)
        if fetch_proc.returncode != 0:
            raise RuntimeError(f"git fetch failed: {stderr.decode('utf-8', errors='replace')}")

        checkout_proc = await asyncio.create_subprocess_exec(
            "git",
            "checkout",
            "FETCH_HEAD",
            cwd=str(target_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await checkout_proc.communicate()


class ReviewWorker:
    """Consumes ReviewJobs from JobQueue and executes ReviewPipeline in isolated temp dirs."""

    def __init__(
        self,
        job_queue: JobQueue,
        github_client: GitHubClient,
        pipeline: ReviewPipeline,
        workspace_provider: WorkspaceProvider,
    ) -> None:
        self._queue = job_queue
        self._github = github_client
        self._pipeline = pipeline
        self._workspace_provider = workspace_provider

    async def process_next(self) -> bool:
        """Process one queued job. Returns True if a job was processed, False if queue empty."""
        job = await self._queue.dequeue()
        if job is None:
            return False

        temp_dir = Path(tempfile.mkdtemp(prefix="pr_review_"))
        try:
            if await self._queue.is_cancelled(job.job_key):
                await self._queue.mark_status(job.job_key, JobStatus.CANCELLED)
                return True

            pr_meta = await self._github.get_pull_request(
                job.repo_full_name,
                job.pr_number,
                job.installation_id,
            )
            if job.head_sha == "pending_fetch":
                job.head_sha = str((pr_meta.get("head") or {}).get("sha", job.head_sha))

            raw_diff = await self._github.get_pull_request_diff(
                job.repo_full_name,
                job.pr_number,
                job.installation_id,
            )
            await self._workspace_provider.checkout(job, temp_dir)

            if await self._queue.is_cancelled(job.job_key):
                await self._queue.mark_status(job.job_key, JobStatus.CANCELLED)
                return True

            await self._pipeline.execute(
                job=job,
                workspace_root=temp_dir,
                raw_diff=raw_diff,
                pr_title=str(pr_meta.get("title") or ""),
                pr_description=str(pr_meta.get("body") or ""),
            )
            await self._queue.mark_status(job.job_key, JobStatus.COMPLETED)
            return True

        except Exception:
            await self._queue.mark_status(job.job_key, JobStatus.FAILED)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

