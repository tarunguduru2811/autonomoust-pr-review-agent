# Implementation Plan & Execution Roadmap

This plan breaks the **Autonomous GitHub PR Review Agent** into 5 incremental milestones so each layer can be built, tested, and verified independently before moving to the next.

> **Reference:** All modules must follow the invariants in [AGENTS.md](../AGENTS.md) and the architecture in [architecture.md](architecture.md).

---

## Target Repository Structure

```text
autonomous-pr-review-agent/
├── AGENTS.md
├── docs/
├── prompts/                 # Runtime LLM system prompts (triage, specialists, judge)
├── src/
│   ├── webhook/             # GitHub webhook signature verification & 202 ingress
│   ├── queue/               # Redis/BullMQ or ARQ job queue + head_sha cancellation
│   ├── github/              # GitHub App auth, diff fetch, Check Runs, Review API
│   ├── orchestrator/        # Prep, static pre-checks, 2-tier router, RunContext
│   ├── tools/               # Bounded read-only tools (read_file, ripgrep, AST) + cache
│   ├── agents/              # Specialist subagents + Single Judge + Pydantic/Zod schemas
│   ├── db/                  # PostgreSQL models & PR-lifecycle comment ledger
│   └── sandbox/             # Optional ephemeral sandbox runner for tests/linters
└── tests/                   # Unit & integration tests for deterministic components
```

---

## Phase 1: Foundation, Webhook Ingress & GitHub App
**Goal:** Receive real GitHub PR events securely, enqueue jobs, and post a basic Check Run.

- [ ] **Project Setup:** Initialize project, linter, typechecker, test runner, and environment config (`GITHUB_APP_ID`, `GITHUB_PRIVATE_KEY`, `WEBHOOK_SECRET`, `DATABASE_URL`, `REDIS_URL`).
- [ ] **Webhook Gateway (`src/webhook/`):**
  - Verify `X-Hub-Signature-256` using HMAC-SHA256 (constant-time comparison).
  - Filter `pull_request` (`opened`, `synchronize`, `reopened`) and `issue_comment` (`/review`).
  - Return `202 Accepted` immediately.
- [ ] **Job Queue (`src/queue/`):**
  - Enqueue jobs keyed by `(repo_id, pr_number, head_sha)`.
  - Cancel/supersede older in-flight jobs when a new `synchronize` commit arrives.
- [ ] **GitHub Client (`src/github/`):**
  - GitHub App JWT + cached installation token auth.
  - Fetch PR metadata and create/update a **GitHub Check Run** (`in_progress` → `completed` / `neutral`).
- **Verification:** Unit tests for webhook signature verification (valid/invalid/missing) and queue deduplication; manual end-to-end test with `smee.io` or `ngrok` on a test repo.

---

## Phase 2: Workspace Prep, Diff Mapper & Read-Only Tools
**Goal:** Prepare clean PR context, enforce bounded read-only tools, and guarantee valid GitHub inline comment mapping.

- [ ] **Workspace & Diff Prep (`src/orchestrator/`):**
  - Shallow clone/fetch PR branch into an isolated temp directory.
  - Compute diff, strip lockfiles/generated assets (`package-lock.json`, `dist/`, `*.min.js`), and load `.pr-agent.yaml` / `AGENTS.md`.
- [ ] **Diff Line Mapper (`src/github/diff_mapper.py` or `.ts`):**
  - Parse unified diff hunks into valid `(file, line, side)` coordinates.
  - Provide fallback to summary comment if a finding's line is outside the diff hunk.
- [ ] **Bounded Read-Only Tools (`src/tools/`):**
  - `read_file(path, start_line, end_line)` — capped at ~150–200 lines.
  - `search_code(pattern)` — backed by `ripgrep`, capped at top 20 matches.
  - `get_symbol_definition(symbol)` — Tree-sitter AST lookup.
  - `RunContext` with immutable `pr_snapshot`, shared `tool_cache`, and append-only `findings_blackboard`.
- **Verification:** Unit tests for diff hunk parsing/mapping, lockfile filtering, tool line/match caps, and `tool_cache` hit behavior.

---

## Phase 3: Deterministic Pre-Checks, 2-Tier Router & Single-Agent MVP
**Goal:** Ship a working end-to-end review loop using static checks + 1 specialist + Judge before scaling to parallel specialists.

- [ ] **Static Pre-Checks (`src/orchestrator/`):**
  - Run Semgrep / repo linters on changed files and attach structured findings to `pr_snapshot`.
- [ ] **Two-Tier Router (`src/orchestrator/`):**
  - *Tier 1:* Deterministic rules (skip docs-only PRs, path-based specialist triggers).
  - *Tier 2:* Fast LLM triage returning strict JSON `{ invoke: [...], reason: "..." }`.
- [ ] **Structured Schemas & Prompts (`src/agents/`, `prompts/`):**
  - Define strict JSON schemas for findings (`file`, `line`, `side`, `severity`, `confidence`, `title`, `explanation`, `suggested_fix`).
- [ ] **MVP Review Loop:**
  - Run `Logic` specialist (with max step cap ~8–10 and 45s timeout) → pass to **Single Judge** → map lines → post GitHub PR Review.
- **Verification:** Run against 5–10 historical PRs with known bugs; verify zero `422` GitHub API errors and fail-open `neutral` Check Run on simulated LLM timeout.

---

## Phase 4: Parallel Specialists, PostgreSQL Memory & Deduplication
**Goal:** Scale to production quality with parallel specialists and PR-lifecycle memory.

- [ ] **Parallel Specialist Subagents (`src/agents/`):**
  - Add `Security`, `Reliability/Perf`, and `DB/Test` specialists.
  - Dispatch selected specialists concurrently via `asyncio.gather` / `Promise.all` with isolated contexts and shared `RunContext` + prompt caching.
- [ ] **PostgreSQL PR-Lifecycle Ledger (`src/db/`):**
  - Store `pr_reviews` and `review_comments` (`repo_id`, `pr_number`, `head_sha`, `fingerprint`, `comment_id`, `status`).
  - Fetch existing GitHub review threads on `synchronize` and suppress findings whose fingerprint is already `resolved` or `dismissed`.
- [ ] **Judge Cross-Verification:**
  - Give Judge read-only tools to verify high-severity claims, merge duplicates across specialists, and drop style nitpicks.
- **Verification:** Integration test simulating a 2-commit PR (`sha_1` → developer resolves comment → `sha_2`) confirming suppressed duplicates and updated DB state.

---

## Phase 5: Sandboxed Execution, Observability & Evals
**Goal:** Harden for multi-repo production traffic, optional test/build execution, and continuous quality tracking.

- [ ] **Sandboxed Execution (`src/sandbox/` — if running tests/linters):**
  - Run repo test/build commands inside ephemeral, network-restricted containers/microVMs with zero host secrets and strict CPU/memory/time limits.
- [ ] **Observability & Cost Tracking:**
  - Instrument OpenTelemetry + Langfuse/Braintrust traces (latency, token cost per PR, tool call counts, specialist timeouts).
- [ ] **Feedback & Eval Loop:**
  - Track `👍`/`👎` reactions and thread resolution rates in PostgreSQL.
  - Build an offline regression benchmark of real PRs to test prompt/model upgrades before deployment.
- **Verification:** Load test concurrent webhooks, verify sandbox blocks secret/network access, and run offline eval suite in CI.

