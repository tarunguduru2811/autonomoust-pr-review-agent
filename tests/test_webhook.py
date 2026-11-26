"""Unit and integration tests for webhook signature verification and ingress."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from pr_agent.config import Settings
from pr_agent.queue import InMemoryJobQueue
from pr_agent.webhook import create_app, verify_github_signature

SECRET = "super-secret-webhook-key"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pr_payload(action: str = "opened", sha: str = "abc123", draft: bool = False) -> dict:
    return {
        "action": action,
        "installation": {"id": 99},
        "repository": {"id": 1001, "full_name": "acme/backend"},
        "pull_request": {
            "number": 42,
            "draft": draft,
            "head": {"sha": sha},
        },
    }


def test_verify_github_signature() -> None:
    body = b'{"hello":"world"}'
    sig = _sign(body)
    assert verify_github_signature(body, SECRET, sig) is True
    assert verify_github_signature(body, SECRET, "sha256=wrong") is False
    assert verify_github_signature(body, SECRET, None) is False
    assert verify_github_signature(b'{"tampered":true}', SECRET, sig) is False


def test_webhook_rejects_invalid_signature() -> None:
    queue = InMemoryJobQueue()
    app = create_app(Settings(github_webhook_secret=SECRET), job_queue=queue)
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert resp.status_code == 401


def test_webhook_accepts_pr_opened_and_enqueues() -> None:
    queue = InMemoryJobQueue()
    app = create_app(Settings(github_webhook_secret=SECRET), job_queue=queue)
    client = TestClient(app)

    body = json.dumps(_pr_payload(action="opened", sha="sha_1")).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data == {"accepted": True, "enqueued": True, "job_key": "1001:42:sha_1"}


def test_webhook_ignores_draft_pr() -> None:
    queue = InMemoryJobQueue()
    app = create_app(Settings(github_webhook_secret=SECRET), job_queue=queue)
    client = TestClient(app)

    body = json.dumps(_pr_payload(action="opened", draft=True)).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 202
    assert resp.json() == {"accepted": False, "reason": "ignored_event"}


def test_webhook_accepts_review_comment_on_pr() -> None:
    queue = InMemoryJobQueue()
    app = create_app(Settings(github_webhook_secret=SECRET), job_queue=queue)
    client = TestClient(app)

    payload = {
        "action": "created",
        "installation": {"id": 99},
        "repository": {"id": 1001, "full_name": "acme/backend"},
        "issue": {"number": 42, "pull_request": {"url": "https://api.github.com/..."}},
        "comment": {"body": "Please /review this carefully"},
    }
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert resp.status_code == 202
    assert resp.json()["accepted"] is True

