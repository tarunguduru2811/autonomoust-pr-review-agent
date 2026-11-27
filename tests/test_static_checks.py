"""Tests for deterministic static pre-checks."""

from pr_agent.orchestrator.static_checks import scan_diff_for_secrets

SECRET_DIFF = """diff --git a/config.py b/config.py
+++ b/config.py
@@ -1,1 +1,2 @@
+AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
"""


def test_scan_diff_for_secrets_detects_aws_key() -> None:
    findings = scan_diff_for_secrets(SECRET_DIFF)
    assert len(findings) == 1
    assert findings[0].rule_id == "aws_access_key"
    assert findings[0].file == "config.py"
    assert findings[0].line == 1

