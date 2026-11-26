# Tech Stack, Security & Operations

## Recommended Tech Stack

| Layer | Purpose | Recommended Tools |
| :--- | :--- | :--- |
| **GitHub Integration** | Auth, webhooks, inline reviews, Check Runs | **GitHub App**, Octokit (`@octokit/app` in TS or `PyGithub` / `gidgethub` in Python) |
| **Webhook Gateway** | Fast signature validation & job enqueueing | FastAPI (Python) or Fastify / Hono (TypeScript) |
| **Queue & Orchestration** | Async retries, deduplication, stale job cancellation | **BullMQ + Redis** (TS), **Celery / ARQ + Redis** (Python), or **Temporal / Inngest** |
| **Static Pre-Checks** | Cheap deterministic bug/security/lint detection | **Tree-sitter** (AST), **Semgrep** (security), **Ruff / ESLint / tsc** |
| **Code Retrieval** | Fast exact lexical & symbol search | **ripgrep**, **Tree-sitter** (symbol index) |
| **Sandboxed Execution** *(if running tests/builds)* | Isolate untrusted PR code | Rootless **Docker**, **Firecracker / gVisor**, **E2B**, or **Modal** |
| **LLM & Agent Layer** | Triage, specialist loops, Judge, structured JSON | Tiered models (fast model for triage, strong model for review/judge); **PydanticAI / LangGraph** or custom tool loop |
| **State & Cache** | PR comment ledger, feedback stats, `RunContext` cache | **PostgreSQL** + **Redis** |
| **Observability & Evals** | Token cost, latency, tool traces, precision tracking | **OpenTelemetry**, **Langfuse** or **Braintrust**, **Sentry** |

---

## Security & Trust Boundaries

1. **Webhook Signature Verification:** Always verify `X-Hub-Signature-256` with the GitHub App webhook secret before enqueueing jobs.
2. **Least-Privilege GitHub App Permissions:** Request only `pull_requests: write`, `contents: read`, `checks: write`, and `metadata: read`.
3. **Prompt Injection Defense:** Treat PR titles, descriptions, commit messages, and code comments as **untrusted data**. Subagents only have read-only codebase tools and cannot directly post to GitHub or run arbitrary shell commands.
4. **Fork & Untrusted Code Sandboxing:** Never execute PR code or untrusted linters/tests in the main backend container; run them in ephemeral, network-restricted sandboxes.

---

## Reliability, Cost & Observability

- **GitHub Rate Limits:** Cache GitHub App installation tokens (1-hour validity), use `ETag` conditional requests where possible, and apply exponential backoff for secondary rate limits.
- **Large PR Handling:** Skip generated/vendor/lockfiles and prioritize high-risk files (auth, DB, API handlers) within token budgets.
- **Offline Evaluation Suite:** Maintain a curated benchmark of historical PRs with known bugs and false-positive traps to validate prompt and model updates before production rollout.
- **Live Precision Metrics:** Monitor `👍` / `👎` reactions, resolved vs. dismissed review threads, and follow-up fix commits to continuously tune signal-to-noise ratio.

