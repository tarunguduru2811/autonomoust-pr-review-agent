"""Two-tier PR router: deterministic rules first, lightweight LLM triage second."""

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

SpecialistName = Literal["logic", "security", "reliability", "db"]
ALLOWED_SPECIALISTS: frozenset[str] = frozenset({"logic", "security", "reliability", "db"})

_DOC_EXTENSIONS = frozenset({".md", ".txt", ".rst", ".adoc"})
_SECURITY_HINTS = ("auth", "route", "controller", "middleware", "security", "token", "jwt")
_DB_HINTS = ("migration", "schema.prisma", "models")


class RouterDecision(BaseModel):
    """Structured routing output consumed by the Orchestrator."""

    skip_review: bool = False
    invoke: list[SpecialistName] = Field(default_factory=list)
    reason: str
    used_llm_triage: bool = False


class TriageLLM(Protocol):
    """Interface for a lightweight Tier-2 triage model."""

    async def classify(self, pr_title: str, pr_description: str, changed_files: list[str]) -> str:
        """Return a JSON string matching RouterDecision."""
        ...


class TwoTierRouter:
    """Selects which specialist subagents the Orchestrator should invoke."""

    def __init__(self, triage_llm: TriageLLM | None = None) -> None:
        self._triage_llm = triage_llm

    def route_deterministic(self, changed_files: list[str]) -> tuple[RouterDecision, bool]:
        """Tier 1: Fast path/extension rules. Returns (decision, needs_llm_triage)."""
        if not changed_files:
            return RouterDecision(skip_review=True, reason="No reviewable files changed."), False

        if all(Path(f).suffix.lower() in _DOC_EXTENSIONS for f in changed_files):
            return (
                RouterDecision(skip_review=True, reason="Documentation-only PR."),
                False,
            )

        selected: list[SpecialistName] = ["logic"]
        needs_triage = False

        for file_path in changed_files:
            lower = file_path.lower()
            if any(h in lower for h in _SECURITY_HINTS) or lower.endswith(
                ("dockerfile", ".tf")
            ):
                if "security" not in selected:
                    selected.append("security")
            elif any(h in lower for h in _DB_HINTS) or lower.endswith(".sql"):
                if "db" not in selected:
                    selected.append("db")
            else:
                needs_triage = True

        return (
            RouterDecision(
                skip_review=False,
                invoke=selected,
                reason="Tier-1 deterministic path rules.",
            ),
            needs_triage,
        )

    async def route(
        self,
        pr_title: str,
        pr_description: str,
        changed_files: list[str],
    ) -> RouterDecision:
        """Run Tier 1 rules, then optionally refine with Tier 2 LLM triage."""
        decision, needs_triage = self.route_deterministic(changed_files)
        if decision.skip_review or not needs_triage or self._triage_llm is None:
            return decision

        raw_json = await self._triage_llm.classify(pr_title, pr_description, changed_files)
        try:
            llm_decision = RouterDecision.model_validate_json(raw_json)
        except Exception:
            # Fail safe: fall back to Tier-1 selection if LLM output is malformed.
            return decision

        if llm_decision.skip_review and not decision.invoke:
            return llm_decision

        merged: list[SpecialistName] = list(decision.invoke)
        for name in llm_decision.invoke:
            if name in ALLOWED_SPECIALISTS and name not in merged:
                merged.append(name)

        return RouterDecision(
            skip_review=False,
            invoke=merged,
            reason=llm_decision.reason or decision.reason,
            used_llm_triage=True,
        )
