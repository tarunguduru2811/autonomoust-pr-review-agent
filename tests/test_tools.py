"""Tests for bounded read-only codebase tools and RunContext tool caching."""

from pathlib import Path

import pytest

from pr_agent.orchestrator.run_context import CandidateFinding, PRSnapshot, RunContext
from pr_agent.tools import MAX_READ_LINES, MAX_SEARCH_MATCHES, CodebaseTools


def _make_context() -> RunContext:
    snapshot = PRSnapshot(
        repo_full_name="acme/backend",
        pr_number=10,
        head_sha="sha123",
        cleaned_diff="",
        changed_files=["app.py"],
    )
    return RunContext(run_id="run-1", pr_snapshot=snapshot)


async def test_read_file_enforces_line_cap_and_path_traversal(tmp_path: Path) -> None:
    big_file = tmp_path / "big.py"
    big_file.write_text("\n".join(f"line_{i}" for i in range(1, 301)), encoding="utf-8")

    ctx = _make_context()
    tools = CodebaseTools(tmp_path, run_context=ctx)

    res = await tools.read_file("big.py", start_line=1, end_line=290)
    assert res.end_line == MAX_READ_LINES
    assert res.truncated is True
    assert "1: line_1" in res.content
    assert f"{MAX_READ_LINES}: line_{MAX_READ_LINES}" in res.content

    # Second identical read hits RunContext cache even if file is removed on disk.
    big_file.unlink()
    cached_res = await tools.read_file("big.py", start_line=1, end_line=290)
    assert cached_res.content == res.content

    with pytest.raises(ValueError, match="escapes workspace root"):
        await tools.read_file("../etc/passwd")


async def test_search_code_and_symbol_definition_are_bounded(tmp_path: Path) -> None:
    lines = ["def target_fn():\n    return 'needle'\n"]
    lines.extend(f"x_{i} = 'needle'\n" for i in range(30))
    (tmp_path / "sample.py").write_text("".join(lines), encoding="utf-8")

    ctx = _make_context()
    tools = CodebaseTools(tmp_path, run_context=ctx)

    search_res = await tools.search_code("needle")
    assert len(search_res.matches) == MAX_SEARCH_MATCHES
    assert search_res.truncated is True

    sym_res = await tools.get_symbol_definition("target_fn")
    assert len(sym_res.matches) == 1
    assert sym_res.matches[0].line == 1


async def test_run_context_findings_blackboard() -> None:
    ctx = _make_context()
    finding = CandidateFinding(
        agent_name="logic",
        file="app.py",
        line=12,
        severity="high",
        confidence=0.9,
        category="null_check",
        title="Missing None guard",
        explanation="order may be None here.",
    )
    await ctx.add_findings([finding])
    assert len(ctx.get_findings()) == 1
    assert ctx.get_findings()[0].agent_name == "logic"
