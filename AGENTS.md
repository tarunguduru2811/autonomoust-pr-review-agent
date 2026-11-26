# AGENTS.md — Engineering Instructions for `autonomous-pr-review-agent`

This file provides system-level instructions for AI coding agents and contributors building the **Autonomous GitHub PR Review Agent** in this repository.

## Source of Truth
Before designing or implementing any module, read the architecture docs in `docs/`:
- `docs/architecture.md` — Orchestrator–Workers (Map–Reduce) HLD and component boundaries.
- `docs/pipeline-flow.md` — End-to-end PR review lifecycle.
- `docs/memory-and-context.md` — Working memory (`RunContext`), PR-lifecycle ledger (PostgreSQL), and repo-level rules.
- `docs/tech-stack-and-security.md` — Stack, GitHub App permissions, sandboxing, and observability.

---

## Core Architectural Invariants (Must Never Be Violated)

1. **Code Owns Control Flow (No Autonomous Spawning)**
   - The **Orchestrator** (application code) owns webhook ingestion, queueing, stale-job cancellation, routing, parallel subagent invocation, timeouts, and GitHub API publishing.
   - LLMs never directly call GitHub write APIs, enqueue jobs, or spawn other agents.

2. **Subagents Are Isolated, Read-Only, and Bounded**
   - Specialist subagents (`Logic`, `Security`, `Reliability/Perf`, `DB/Test`, etc.) run in parallel and **never** share raw LLM conversation histories with each other.
   - Subagents only receive **read-only codebase tools** (`read_file`, `search_code`, `get_symbol_definition`).
   - Every tool output must be bounded (e.g., max ~150–200 lines for `read_file`, top 20 matches for `search_code`), and every subagent run must enforce a hard step limit and timeout.

3. **Shared `RunContext` Scope**
   - Share only: (a) the immutable `pr_snapshot` (cleaned diff, repo rules, static pre-checks, existing PR threads), (b) a `tool_cache` for deduplicating identical file/search reads during a run, and (c) an append-only `findings_blackboard` for structured JSON outputs.

4. **Single Judge & Diff Line Mapping**
   - All specialist findings must pass through a **single Judge** that verifies claims against the code, deduplicates overlapping findings, drops low-confidence/style nitpicks, and suppresses issues already marked `resolved` or `dismissed` in PostgreSQL.
   - Every inline comment must be validated by a deterministic **Diff Line Mapper** against the PR diff hunks before calling the GitHub Review API.

5. **Strict Structured Outputs**
   - All router decisions, specialist findings, and Judge outputs must use strict schema validation (e.g., Pydantic or Zod). Never pass unvalidated free-form markdown between pipeline stages.

---

## Security & Reliability Rules

- **Webhook Security:** Always verify `X-Hub-Signature-256` before enqueueing. Return `202 Accepted` immediately and process asynchronously.
- **Concurrency & Idempotency:** Key jobs by `(repo_id, pr_number, head_sha)`. Cancel or supersede in-flight jobs when a newer `synchronize` event arrives for the same PR.
- **Prompt Injection Defense:** Treat PR titles, descriptions, commit messages, and source code comments as untrusted data. Never interpolate them into privileged system instructions without clear data delimiters.
- **Sandboxed Execution:** If executing any repository code, tests, builds, or project-configured linters (`npm test`, `pytest`, `eslint`, etc.), run them in an ephemeral, network-restricted sandbox with **zero** host secrets (no GitHub App private key, DB URL, or LLM API keys inside the sandbox).
- **Fail-Open Publishing:** Never block PR merges due to internal agent/LLM outages; degrade gracefully (e.g., post a `neutral` Check Run status and log to observability).

---

## Code & Documentation Standards

- Keep modules single-purpose with clear boundaries between `webhook/`, `queue/`, `orchestrator/`, `agents/`, `tools/`, `github/`, and `db/`.
- Write unit and integration tests for all deterministic components (webhook signature verification, diff hunk mapper, Tier-1 router rules, fingerprint deduplication, tool output bounds).
- Keep `docs/` updated whenever architectural boundaries or data schemas change.

