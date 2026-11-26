"""GitHub webhook HMAC-SHA256 signature verification."""

import hashlib
import hmac


def verify_github_signature(
    payload_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verify X-Hub-Signature-256 header using constant-time HMAC comparison."""
    if not secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)

