"""Tests for TwoTierRouter deterministic rules and Tier-2 LLM triage."""

import json

from pr_agent.orchestrator.router import TwoTierRouter


class _FakeTriageLLM:
    def __init__(self, response: str) -> None:
        self._response = response
        self.called = False

    async def classify(self, pr_title: str, pr_description: str, changed_files: list[str]) -> str:
        self.called = True
        return self._response


async def test_router_skips_docs_only_pr_without_calling_llm() -> None:
    llm = _FakeTriageLLM("{}")
    router = TwoTierRouter(triage_llm=llm)

    decision = await router.route("Update docs", "Fix typo", ["README.md", "docs/arch.txt"])
    assert decision.skip_review is True
    assert decision.invoke == []
    assert llm.called is False


async def test_router_triggers_security_and_db_deterministically() -> None:
    llm = _FakeTriageLLM("{}")
    router = TwoTierRouter(triage_llm=llm)

    decision = await router.route(
        "Auth + SQL",
        "",
        ["src/auth/login.py", "db/migrations/001.sql"],
    )
    assert decision.skip_review is False
    assert set(decision.invoke) == {"logic", "security", "db"}
    assert llm.called is False


async def test_router_uses_llm_triage_for_ambiguous_files_and_validates_allowlist() -> None:
    llm = _FakeTriageLLM(
        json.dumps(
            {
                "skip_review": False,
                "invoke": ["logic", "reliability"],
                "reason": "Loops over DB queries",
            }
        )
    )
    router = TwoTierRouter(triage_llm=llm)

    decision = await router.route("Refactor processor", "", ["src/services/processor.py"])
    assert llm.called is True
    assert decision.used_llm_triage is True
    assert decision.invoke == ["logic", "reliability"]

