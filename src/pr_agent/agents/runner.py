"""Bounded Specialist and Judge agent runners."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from pr_agent.agents.schemas import FinalReviewComment, JudgeOutput, SpecialistOutput
from pr_agent.orchestrator.run_context import CandidateFinding, RunContext
from pr_agent.tools import CodebaseTools

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


class ToolCallRequest(BaseModel):
    """Read-only tool invocation requested by an LLM turn."""

    tool: Literal["read_file", "search_code", "get_symbol_definition"]
    args: dict[str, Any] = Field(default_factory=dict)


class LLMTurnResponse(BaseModel):
    """Single turn response from an AgentLLM: either tool calls or final JSON."""

    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    final_json: str | None = None


class AgentLLM(Protocol):
    """Provider-agnostic interface for tool-calling specialist and Judge models."""

    async def step(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> LLMTurnResponse:
        """Execute one model turn and return tool requests or final JSON."""
        ...


def compute_fingerprint(file: str, category: str, title: str) -> str:
    """Deterministic fingerprint for comment deduplication across commits."""
    raw = f"{file}|{category}|{title.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_prompt(name: str) -> str:
    """Load a runtime system prompt from prompts/<name>.md if present."""
    path = _PROMPTS_DIR / f"{name}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return f"You are the {name} PR review agent. Return valid JSON only."


class _BoundedToolExecutor:
    """Dispatches only read-only codebase tools."""

    def __init__(self, tools: CodebaseTools) -> None:
        self._tools = tools

    async def execute(self, req: ToolCallRequest) -> str:
        try:
            if req.tool == "read_file":
                res = await self._tools.read_file(
                    path=str(req.args.get("path", "")),
                    start_line=int(req.args.get("start_line", 1)),
                    end_line=req.args.get("end_line"),
                )
                return res.model_dump_json()
            if req.tool == "search_code":
                res = await self._tools.search_code(
                    pattern=str(req.args.get("pattern", "")),
                    file_glob=req.args.get("file_glob"),
                )
                return res.model_dump_json()
            if req.tool == "get_symbol_definition":
                res = await self._tools.get_symbol_definition(
                    symbol=str(req.args.get("symbol", "")),
                )
                return res.model_dump_json()
        except Exception as exc:
            return f'{{"error": "{type(exc).__name__}: {exc}"}}'
        return '{"error": "Unsupported tool"}'


class SpecialistRunner:
    """Runs a single specialist subagent with hard step and timeout bounds."""

    def __init__(
        self,
        llm: AgentLLM,
        tools: CodebaseTools,
        max_steps: int = 10,
        timeout_seconds: float = 45.0,
    ) -> None:
        self._llm = llm
        self._executor = _BoundedToolExecutor(tools)
        self._max_steps = max_steps
        self._timeout = timeout_seconds

    async def run(self, agent_name: str, run_context: RunContext) -> list[CandidateFinding]:
        """Execute a specialist loop and append findings to RunContext."""
        return await asyncio.wait_for(
            self._run_loop(agent_name, run_context),
            timeout=self._timeout,
        )

    async def _run_loop(self, agent_name: str, run_context: RunContext) -> list[CandidateFinding]:
        system_prompt = load_prompt(agent_name)
        snapshot = run_context.pr_snapshot
        user_payload = (
            f"<pr_metadata>\ntitle: {snapshot.title}\n"
            f"description: {snapshot.description}\n</pr_metadata>\n"
            f"<repo_rules>\n{snapshot.repo_rules}\n</repo_rules>\n"
            f"<static_findings>\n{snapshot.static_findings}\n</static_findings>\n"
            f"<diff>\n{snapshot.cleaned_diff}\n</diff>"
        )
        messages: list[dict[str, str]] = [{"role": "user", "content": user_payload}]

        for _ in range(self._max_steps):
            turn = await self._llm.step(system_prompt, messages)
            if turn.final_json is not None:
                parsed = SpecialistOutput.model_validate_json(turn.final_json)
                await run_context.add_findings(parsed.findings)
                return parsed.findings

            if not turn.tool_calls:
                break

            tool_results = await asyncio.gather(
                *(self._executor.execute(tc) for tc in turn.tool_calls)
            )
            messages.append(
                {
                    "role": "tool",
                    "content": "\n".join(tool_results),
                }
            )

        return []


class JudgeRunner:
    """Single Judge that verifies, deduplicates, and filters candidate findings."""

    def __init__(
        self,
        llm: AgentLLM | None,
        tools: CodebaseTools,
        max_steps: int = 6,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._llm = llm
        self._executor = _BoundedToolExecutor(tools)
        self._max_steps = max_steps
        self._timeout = timeout_seconds

    async def evaluate(self, run_context: RunContext) -> JudgeOutput:
        """Synthesize RunContext findings into a verified JudgeOutput."""
        candidates = run_context.get_findings()
        if not candidates:
            return JudgeOutput(summary="No issues found.", comments=[], dropped_count=0)

        if self._llm is None:
            return self._deterministic_filter(candidates, run_context)

        return await asyncio.wait_for(
            self._run_llm_judge(candidates, run_context),
            timeout=self._timeout,
        )

    async def _run_llm_judge(
        self,
        candidates: list[CandidateFinding],
        run_context: RunContext,
    ) -> JudgeOutput:
        system_prompt = load_prompt("judge")
        snapshot = run_context.pr_snapshot
        serialized_candidates = [c.model_dump() for c in candidates]
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": (
                    f"<existing_threads>{snapshot.existing_threads}</existing_threads>\n"
                    f"<candidates>{serialized_candidates}</candidates>\n"
                    f"<diff>{snapshot.cleaned_diff}</diff>"
                ),
            }
        ]

        assert self._llm is not None
        for _ in range(self._max_steps):
            turn = await self._llm.step(system_prompt, messages)
            if turn.final_json is not None:
                return JudgeOutput.model_validate_json(turn.final_json)
            if not turn.tool_calls:
                break
            tool_results = await asyncio.gather(
                *(self._executor.execute(tc) for tc in turn.tool_calls)
            )
            messages.append({"role": "tool", "content": "\n".join(tool_results)})

        return self._deterministic_filter(candidates, run_context)

    def _deterministic_filter(
        self,
        candidates: list[CandidateFinding],
        run_context: RunContext,
    ) -> JudgeOutput:
        """Fallback deterministic deduplication & confidence filter."""
        dismissed_fps = {
            t.get("fingerprint")
            for t in run_context.pr_snapshot.existing_threads
            if t.get("status") in ("resolved", "dismissed")
        }
        seen_fps: set[str] = set()
        kept: list[FinalReviewComment] = []
        dropped = 0

        for c in candidates:
            fp = compute_fingerprint(c.file, c.category, c.title)
            if c.confidence < 0.6 or fp in dismissed_fps or fp in seen_fps:
                dropped += 1
                continue
            seen_fps.add(fp)
            kept.append(
                FinalReviewComment(
                    file=c.file,
                    line=c.line,
                    side=c.side,
                    severity=c.severity,
                    category=c.category,
                    title=c.title,
                    body=c.explanation,
                    suggested_fix=c.suggested_fix,
                    fingerprint=fp,
                )
            )

        summary = (
            f"Found {len(kept)} issue(s) across the PR."
            if kept
            else "No high-confidence issues found."
        )
        return JudgeOutput(summary=summary, comments=kept, dropped_count=dropped)
