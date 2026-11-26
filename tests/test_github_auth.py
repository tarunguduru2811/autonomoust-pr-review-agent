"""Tests for GitHub App JWT generation, token caching, and Check Run creation."""

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from pr_agent.github import CheckRunConclusion, CheckRunStatus, GitHubAppAuth, GitHubClient


def _generate_test_rsa_pem() -> tuple[str, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, private_key.public_key()


def test_generate_app_jwt_valid_rs256() -> None:
    pem, pub_key = _generate_test_rsa_pem()
    auth = GitHubAppAuth(app_id="12345", private_key_pem=pem)
    token = auth.generate_app_jwt(now=1_700_000_000)

    decoded = jwt.decode(token, pub_key, algorithms=["RS256"], options={"verify_exp": False})
    assert decoded["iss"] == "12345"
    assert decoded["iat"] == 1_699_999_940
    assert decoded["exp"] == 1_700_000_540


async def test_installation_token_is_cached_and_check_run_posted() -> None:
    pem, _ = _generate_test_rsa_pem()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "ghs_test_token"})
        if request.url.path.endswith("/check-runs"):
            return httpx.Response(201, json={"id": 555, "status": "completed"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        auth = GitHubAppAuth(app_id="12345", private_key_pem=pem, http_client=http_client)
        gh = GitHubClient(auth=auth, http_client=http_client)

        res1 = await gh.create_check_run(
            repo_full_name="acme/backend",
            head_sha="abc123",
            installation_id=99,
            status=CheckRunStatus.COMPLETED,
            conclusion=CheckRunConclusion.NEUTRAL,
            summary="No issues found.",
        )
        res2 = await gh.create_check_run(
            repo_full_name="acme/backend",
            head_sha="def456",
            installation_id=99,
        )

    assert res1["id"] == 555
    assert res2["id"] == 555
    # /access_tokens should only be called once due to caching.
    token_calls = [c for c in calls if c[1].endswith("/access_tokens")]
    assert len(token_calls) == 1
