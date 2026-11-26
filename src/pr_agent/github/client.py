"""Async GitHub REST API client for Check Runs and Pull Request metadata."""

from enum import StrEnum
from typing import Any

import httpx

from pr_agent.github.auth import GitHubAppAuth


class CheckRunStatus(StrEnum):
    """GitHub Check Run status values."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class CheckRunConclusion(StrEnum):
    """GitHub Check Run conclusion values."""

    SUCCESS = "success"
    NEUTRAL = "neutral"
    ACTION_REQUIRED = "action_required"


class GitHubClient:
    """Thin async wrapper around GitHub REST APIs needed by the review agent."""

    def __init__(
        self,
        auth: GitHubAppAuth,
        api_base_url: str = "https://api.github.com",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client = http_client

    async def _request(
        self,
        method: str,
        path: str,
        installation_id: int,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._auth.get_installation_token(installation_id)
        url = f"{self._api_base_url}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._http_client is not None:
            resp = await self._http_client.request(method, url, headers=headers, json=json_body)
        else:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)

        resp.raise_for_status()
        return resp.json()

    async def get_pull_request(
        self,
        repo_full_name: str,
        pr_number: int,
        installation_id: int,
    ) -> dict[str, Any]:
        """Fetch PR metadata (title, body, base/head refs, draft state)."""
        return await self._request(
            "GET",
            f"/repos/{repo_full_name}/pulls/{pr_number}",
            installation_id,
        )

    async def create_check_run(
        self,
        repo_full_name: str,
        head_sha: str,
        installation_id: int,
        name: str = "Autonomous PR Review",
        status: CheckRunStatus = CheckRunStatus.IN_PROGRESS,
        conclusion: CheckRunConclusion | None = None,
        summary: str = "Review in progress...",
    ) -> dict[str, Any]:
        """Create or update a GitHub Check Run for the PR's head commit."""
        payload: dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status.value,
            "output": {
                "title": name,
                "summary": summary,
            },
        }
        if conclusion is not None:
            payload["conclusion"] = conclusion.value

        return await self._request(
            "POST",
            f"/repos/{repo_full_name}/check-runs",
            installation_id,
            json_body=payload,
        )

    async def create_pull_request_review(
        self,
        repo_full_name: str,
        pr_number: int,
        commit_id: str,
        installation_id: int,
        body: str,
        comments: list[dict[str, Any]] | None = None,
        event: str = "COMMENT",
    ) -> dict[str, Any]:
        """Post a Pull Request Review with summary body and validated inline comments."""
        payload: dict[str, Any] = {
            "commit_id": commit_id,
            "body": body,
            "event": event,
            "comments": comments or [],
        }
        return await self._request(
            "POST",
            f"/repos/{repo_full_name}/pulls/{pr_number}/reviews",
            installation_id,
            json_body=payload,
        )

