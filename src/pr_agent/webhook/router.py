"""FastAPI router for GitHub webhook events."""

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from pr_agent.queue import JobQueue, ReviewJob
from pr_agent.webhook.security import verify_github_signature

_SUPPORTED_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def create_webhook_router(
    webhook_secret: str,
    job_queue: JobQueue,
    trigger_command: str = "/review",
) -> APIRouter:
    """Create the GitHub webhook router bound to a queue and secret."""
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_body = await request.body()
        if not verify_github_signature(raw_body, webhook_secret, x_hub_signature_256):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature.",
            )

        payload: dict[str, Any] = await request.json()
        job = _extract_review_job(
            event_type=x_github_event,
            payload=payload,
            trigger_command=trigger_command,
        )
        if job is None:
            return {"accepted": False, "reason": "ignored_event"}

        enqueued = await job_queue.enqueue(job)
        return {
            "accepted": True,
            "enqueued": enqueued,
            "job_key": job.job_key,
        }

    return router


def _extract_review_job(
    event_type: str | None,
    payload: dict[str, Any],
    trigger_command: str,
) -> ReviewJob | None:
    """Parse supported GitHub events into a ReviewJob, or return None to ignore."""
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    repo = payload.get("repository") or {}
    repo_id = repo.get("id")
    repo_full_name = repo.get("full_name")

    if not installation_id or not repo_id or not repo_full_name:
        return None

    action = payload.get("action")

    if event_type == "pull_request" and action in _SUPPORTED_PR_ACTIONS:
        pr = payload.get("pull_request") or {}
        if pr.get("draft"):
            return None
        pr_number = pr.get("number")
        head_sha = (pr.get("head") or {}).get("sha")
        if not pr_number or not head_sha:
            return None
        return ReviewJob(
            repo_id=repo_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            installation_id=installation_id,
            trigger_event=f"pull_request.{action}",
        )

    if event_type == "issue_comment" and action == "created":
        issue = payload.get("issue") or {}
        comment_body = (payload.get("comment") or {}).get("body", "").strip()
        # Only trigger on PR comments (issues with pull_request key) containing /review.
        if "pull_request" not in issue or trigger_command not in comment_body:
            return None
        pr_number = issue.get("number")
        if not pr_number:
            return None
        return ReviewJob(
            repo_id=repo_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha="pending_fetch",
            installation_id=installation_id,
            trigger_event="issue_comment.review",
        )

    return None

