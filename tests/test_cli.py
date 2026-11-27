"""Tests for CLI local review runner."""

from pathlib import Path

import pytest

from pr_agent.cli import run_local_review
from pr_agent.config import Settings


async def test_run_local_review_requires_gemini_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        await run_local_review(
            repo_path=tmp_path,
            base="HEAD~1",
            head="HEAD",
            title="Test",
            settings=Settings(gemini_api_key=""),
        )

