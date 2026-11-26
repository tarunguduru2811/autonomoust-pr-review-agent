# Memory & Context Management

In a production PR review agent, "memory" is split into **three practical layers**. Heavy conversational memory frameworks (e.g., Mem0, Zep) and stale code vector databases are intentionally avoided in favor of live, deterministic retrieval and relational state.

---

## 1. Working Memory & Shared `RunContext` (Within a Single Run)

Parallel subagents share a scoped `RunContext` (in-memory for single-worker runs, or Redis with a ~1-hour TTL for distributed workers) keyed by `run_id`:

| Component | Access | Purpose |
| :--- | :--- | :--- |
| **Immutable PR Snapshot** | Read-only (all) | Cleaned diff, changed file metadata, `.pr-agent.yaml` / `AGENTS.md`, Semgrep/linter output, existing PR threads. |
| **Shared Tool-Result Cache** | Read/Write cache | Caches `read_file` and `search_code` outputs during the run so if multiple specialists inspect the same file/symbol, disk/search is hit only once. |
| **Findings Blackboard** | Append-only | Each specialist writes its structured JSON candidate findings here for the Judge to consume. |

### Guardrails for Working Memory
- **Private reasoning traces:** Subagents **never** share raw LLM message histories with each other (prevents race conditions, prompt confusion, and token bloat).
- **Bounded tool outputs:** `read_file` returns targeted slices (max ~150–200 lines); `search_code` caps results (e.g., top 20 matches).
- **Prompt caching:** Share the static prefix (`System Prompt + Repo Rules + PR Diff`) across parallel subagents to cut cost and latency.
- **Step & timeout caps:** Each subagent is capped at ~8–10 tool turns and a hard timeout (e.g., 45s).

---

## 2. PR-Lifecycle Memory (Across Commits on the Same PR)

Stored in **PostgreSQL**, keyed by `(repo_id, pr_number)`:

- `last_reviewed_sha`: Tracks the most recently reviewed commit so `synchronize` events know what changed.
- **Fingerprinted Comments Ledger:** Stores `(comment_id, file, line_hash, issue_fingerprint, status)` where `status` is `open`, `resolved`, or `dismissed`.
- **Thread & Reply Awareness:** Before the Judge publishes new comments, existing bot threads are fetched. If a developer resolved a thread or replied *"intentional"*, the Judge suppresses repeat comments on that issue.

---

## 3. Long-Term / Repo Memory (Across Different PRs)

- **Explicit Team Rules:** Load `.pr-agent.yaml` and `AGENTS.md` from the target repository on every run.
- **Feedback-Driven Tuning:** Track `👍` / `👎` reactions and dismissed comment rates per rule/category in PostgreSQL. High-dismissal checks can be automatically down-ranked or disabled for that repository.
- **Live Code Retrieval (No Stale Vector DB):** A fresh shallow checkout combined with `ripgrep` and Tree-sitter provides always-accurate context for every commit. (Semantic/vector indexing is only needed later for massive multi-GB monorepos.)

