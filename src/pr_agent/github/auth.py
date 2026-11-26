"""GitHub App JWT generation and cached installation token management."""

import time
from dataclasses import dataclass

import httpx
import jwt


@dataclass
class _CachedToken:
    token: str
    expires_at: float


class GitHubAppAuth:
    """Handles GitHub App JWT signing and installation access token caching."""

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        api_base_url: str = "https://api.github.com",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client = http_client
        self._token_cache: dict[int, _CachedToken] = {}

    def generate_app_jwt(self, now: int | None = None) -> str:
        """Create a short-lived RS256 JWT (max 10 min) to authenticate as the GitHub App."""
        current = now if now is not None else int(time.time())
        payload = {
            "iat": current - 60,  # Clock-skew buffer
            "exp": current + 540,  # 9 minutes
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key_pem, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        """Return a valid installation token, refreshing only when within 60s of expiry."""
        cached = self._token_cache.get(installation_id)
        if cached and cached.expires_at > time.time() + 60:
            return cached.token

        app_jwt = self.generate_app_jwt()
        url = f"{self._api_base_url}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        if self._http_client is not None:
            resp = await self._http_client.post(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers)

        resp.raise_for_status()
        data = resp.json()
        token = data["token"]
        # GitHub installation tokens last 1 hour; cache for 55 minutes by default.
        self._token_cache[installation_id] = _CachedToken(
            token=token,
            expires_at=time.time() + 3300,
        )
        return token

