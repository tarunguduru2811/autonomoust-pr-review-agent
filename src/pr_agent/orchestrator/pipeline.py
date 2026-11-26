"""End-to-end Orchestrator review pipeline with fail-open publishing."""

import logging
from pathlib import Path
from typing import Any

from pr_agent.agents.runner import AgentLLM, JudgeRunner, SpecialistRunner
from pr_agent.agents.schemas import JudgeOutput
from pr_agent.github.client import CheckRunConclusion, CheckRunStatus, GitHubClient
from pr_agent.github.diff_mapper import DiffLineMapper
from pr_agent.orchestrator.diff_prep import load_repo_rules, prepare_diff
from pr_agent.orchestrator.router import TwoTierRouter
from pr_agent.orchestrator.run_context import PRSnapshot, RunContext
from pr_agent.orchestrator.static_checks import run_static_checks
from pr_agent.queue.models import ReviewJob
from pr_agent.tools import CodebaseTools

logger = logging.getLogger(__name__)


class ReviewPipeline:
    """Orchestrates a single PR review run from diff prep to GitHub publishing."""

    def __init__(
        self,
        github_client: GitHubClient,
        router: TwoTierRouter,
        specialist_llm: AgentLLM,
        judge_llm: AgentLLM | None = None,
    ) -> None:
        self._github = github_client
        self._router = router
        self._specialist_llm = specialist_llm
        self._judge_llm = judge_llm

    async def execute(
        self,
        job: ReviewJob,
        workspace_root: Path,
        raw_diff: str,
        pr_title: str = "",
        pr_description: str = "",
        existing_threads: list[dict[str, Any]] | None = None,
    ) -> JudgeOutput | None:
        """Run the full PR review pipeline with fail-open Check Run handling."""
        await self._github.create_check_run(
            repo_full_name=job.repo_full_name,
            head_sha=job.head_sha,
            installation_id=job.installation_id,
            status=CheckRunStatus.IN_PROGRESS,
            summary="Autonomous PR review in progress...",
        )

        try:
            prepared = prepare_diff(raw_diff)
            changed_files = [f.path for f in prepared.files]
            decision = await self._router.route(pr_title, pr_description, changed_files)

            if decision.skip_review:
                await self._github.create_check_run(
                    repo_full_name=job.repo_full_name,
                    head_sha=job.head_sha,
                    installation_id=job.installation_id,
                    status=CheckRunStatus.COMPLETED,
                    conclusion=CheckRunConclusion.SUCCESS,
                    summary=f"Review skipped: {decision.reason}",
                )
                return None

            static_findings = await run_static_checks(
                workspace_root,
                prepared.cleaned_diff,
                changed_files,
            )
            snapshot = PRSnapshot(
                repo_full_name=job.repo_full_name,
                pr_number=job.pr_number,
                head_sha=job.head_sha,
                title=pr_title,
                description=pr_description,
                cleaned_diff=prepared.cleaned_diff,
                changed_files=changed_files,
                repo_rules=load_repo_rules(workspace_root),
                static_findings=static_findings,
                existing_threads=existing_threads or [],
            )
            run_context = RunContext(run_id=job.job_key, pr_snapshot=snapshot)
            tools = CodebaseTools(workspace_root, run_context=run_context)

            specialist = SpecialistRunner(self._specialist_llm, tools)
            # MVP executes the primary selected specialist (Phase 4 parallelizes all).
            primary_agent = decision.invoke[0] if decision.invoke else "logic"
            await specialist.run(primary_agent, run_context)

            judge = JudgeRunner(self._judge_llm, tools)
            judge_output = await judge.evaluate(run_context)

            await self._publish_review(job, prepared.cleaned_diff, judge_output)
            return judge_output

        except Exception as exc:
            logger.exception("PR review failed for %s: %s", job.job_key, exc)
            # Fail-open: never block PR merges due to internal agent/LLM outages.
            await self._github.create_check_run(
                repo_full_name=job.repo_full_name,
                head_sha=job.head_sha,
                installation_id=job.installation_id,
                status=CheckRunStatus.COMPLETED,
                conclusion=CheckRunConclusion.NEUTRAL,
                summary="Automated review unavailable due to an internal error; merge not blocked.",
            )
            return None

    async def _publish_review(
        self,
        job: ReviewJob,
        cleaned_diff: str,
        judge_output: JudgeOutput,
    ) -> None:
        mapper = DiffLineMapper(cleaned_diff)
        inline_comments: list[dict[str, Any]] = []
        summary_fallbacks: list[str] = []

        for comment in judge_output.comments:
            mapped = mapper.map_location(comment.file, comment.line, comment.side)
            formatted_body = f"**[{comment.severity.upper()}] {comment.title}**\n\n{comment.body}"
            if comment.suggested_fix:
                formatted_body += f"\n\nSuggested fix:\n```\n{comment.suggested_fix}\n```"

            if mapped.in_diff_hunk:
                inline_comments.append(
                    {
                        "path": mapped.file,
                        "line": mapped.line,
                        "side": mapped.side,
                        "body": formatted_body,
                    }
                )
            else:
                summary_fallbacks.append(f"- `{comment.file}:{comment.line}` — {formatted_body}")

        full_body = judge_output.summary
        if summary_fallbacks:
            full_body += "\n\n### Additional Findings (Outside Diff Hunk)\n" + "\n".join(
                summary_fallbacks
            )

        if inline_comments or judge_output.comments:
            await self._github.create_pull_request_review(
                repo_full_name=job.repo_full_name,
                pr_number=job.pr_number,
                commit_id=job.head_sha,
                installation_id=job.installation_id,
                body=full_body,
                comments=inline_comments,
            )

        has_critical = any(c.severity in ("critical", "high") for c in judge_output.comments)
        conclusion = (
            CheckRunConclusion.ACTION_REQUIRED if has_critical else CheckRunConclusion.SUCCESS
        )
        await self._github.create_check_run(
            repo_full_name=job.repo_full_name,
            head_sha=job.head_sha,
            installation_id=job.installation_id,
            status=CheckRunStatus.COMPLETED,
            conclusion=conclusion,
            summary=judge_output.summary,
        )
