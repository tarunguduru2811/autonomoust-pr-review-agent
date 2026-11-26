"""Deterministic static pre-checks run before LLM specialists."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "hardcoded_secret",
        re.compile(r"""(?i)(?:api_key|secret_key|password|token)\s*=\s*["'][^"']{12,}["']"""),
    ),
)


class StaticFinding(BaseModel):
    """Structured finding from a deterministic static analyzer."""

    tool: str
    rule_id: str
    file: str
    line: int
    severity: str
    message: str


def scan_diff_for_secrets(cleaned_diff: str) -> list[StaticFinding]:
    """Fast built-in regex scan on added diff lines for accidental secret leaks."""
    findings: list[StaticFinding] = []
    current_file = ""
    new_line = 0

    for raw in cleaned_diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            continue
        if raw.startswith("@@ "):
            parts = raw.split("+")
            if len(parts) >= 2:
                num_part = parts[1].split(",")[0].split(" ")[0]
                if num_part.isdigit():
                    new_line = int(num_part)
            continue
        if raw.startswith("+") and not raw.startswith("+++ "):
            added_code = raw[1:]
            for rule_id, pattern in _SECRET_PATTERNS:
                if pattern.search(added_code):
                    findings.append(
                        StaticFinding(
                            tool="builtin_secret_scan",
                            rule_id=rule_id,
                            file=current_file,
                            line=max(1, new_line),
                            severity="critical",
                            message=f"Potential hardcoded secret detected ({rule_id}).",
                        )
                    )
            new_line += 1
        elif not raw.startswith("-"):
            new_line += 1

    return findings


async def run_ruff_on_files(workspace_root: Path, changed_files: list[str]) -> list[StaticFinding]:
    """Run ruff JSON check on changed Python files if ruff is installed."""
    py_files = [f for f in changed_files if f.endswith(".py") and (workspace_root / f).is_file()]
    if not py_files:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "ruff",
            "check",
            "--output-format",
            "json",
            *py_files,
            cwd=str(workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    except (FileNotFoundError, TimeoutError):
        return []

    try:
        raw_items: list[dict[str, Any]] = json.loads(stdout.decode("utf-8") or "[]")
    except json.JSONDecodeError:
        return []

    return [
        StaticFinding(
            tool="ruff",
            rule_id=str(item.get("code", "RUFF")),
            file=str(item.get("filename", "")),
            line=int((item.get("location") or {}).get("row", 1)),
            severity="low",
            message=str(item.get("message", "")),
        )
        for item in raw_items[:30]
    ]


async def run_static_checks(
    workspace_root: Path,
    cleaned_diff: str,
    changed_files: list[str],
) -> list[dict[str, Any]]:
    """Run all enabled deterministic pre-checks and return serialized findings."""
    findings = scan_diff_for_secrets(cleaned_diff)
    findings.extend(await run_ruff_on_files(workspace_root, changed_files))
    return [f.model_dump() for f in findings]
