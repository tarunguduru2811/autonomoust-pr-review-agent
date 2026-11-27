# Production-Level Testing Guide

This guide covers all 5 stages required to validate the **Autonomous PR Review Agent** at production quality before deploying.

---

## Stage 1: Deterministic & Unit/Integration Suite
Run fast, offline verification of webhook security, queue cancellation, diff line mapping, tool bounds, router rules, and ledger deduplication:

```bash
uv run ruff check src tests
uv run pytest -v
```

**Pass criteria:** All lint checks and all 28+ tests pass with zero warnings/errors.

---

## Stage 2: Local Real-LLM Benchmark (`review-local`)
Test real Gemini accuracy, multi-file bug detection, and latency on a real git repository before involving GitHub webhooks:

```bash
uv run pr-agent review-local \
  --repo /path/to/target-repo \
  --base main \
  --head feature-branch \
  --title "Test multi-file PR"
```

Verify in the JSON output:
- `specialists_invoked` matches the files changed (e.g., `security` for auth/routes, `db` for SQL/migrations).
- `comments` accurately identify real bugs with valid line numbers.
- `metrics.duration_ms` is within your latency budget.

---

## Stage 3: Production Infrastructure (Redis + PostgreSQL)
Switch from in-memory dev backends to real **Redis** (queue + stale-commit cancellation) and **PostgreSQL** (durable PR-lifecycle comment ledger):

1. Start Redis and PostgreSQL:
   ```bash
   docker run -d --name pr-agent-redis -p 6379:6379 redis:7-alpine
   docker run -d --name pr-agent-pg -p 5432:5432 \
     -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=pr_agent postgres:16-alpine
   ```
2. Update `.env`:
   ```env
   QUEUE_BACKEND=redis
   REDIS_URL=redis://localhost:6379/0
   LEDGER_BACKEND=postgres
   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/pr_agent
   ```
3. Start the server + worker (`ensure_schema()` creates `pr_reviews` and `review_comments` automatically):
   ```bash
   uv run pr-agent serve --host 0.0.0.0 --port 8002
   ```

---

## Stage 4: Real GitHub End-to-End Test Matrix
Connect a test repository via your GitHub App + HTTPS endpoint (or `smee-client`) and verify these **5 production scenarios**:

| # | Scenario | How to Trigger | Expected Result |
| :--- | :--- | :--- | :--- |
| **1** | **Multi-bug detection** | Open a PR changing both an auth route (e.g., missing ownership check) and a SQL migration. | Router invokes `logic`, `security`, and `db` in parallel; inline comments appear on exact lines; Check Run is `action_required`. |
| **2** | **Cross-commit deduplication** | Push a 2nd commit fixing 1 bug while leaving the other unfixed (or adding a new bug). | Bot does **not** re-post the already-commented issue; marks the fixed issue `fixed` in PostgreSQL; posts only the new bug. |
| **3** | **Rapid commit cancellation** | Push 2 commits within 2 seconds (`sha_1` then `sha_2`). | Queue marks `sha_1` `cancelled`; only `sha_2` posts a PR Review. |
| **4** | **Docs-only fast skip** | Open a PR modifying only `README.md`. | Tier-1 router skips LLMs; Check Run completes immediately as `success` in < 2s. |
| **5** | **Fail-open resilience** | Temporarily set an invalid `GEMINI_API_KEY` and push a commit. | Pipeline catches the error, logs it, and posts a **`neutral`** Check Run so merges are never blocked. |

---

## Stage 5: Sandbox & Security Verification
If enabling `SandboxRunner` for executing untrusted repository tests/linters:
1. Verify Docker is available (`docker --version`).
2. Confirm `SandboxRunner.build_docker_command()` enforces `--network=none`, `--read-only`, `--cap-drop=ALL`, and mounts the workspace read-only (`:ro`) with zero host environment secrets (`GITHUB_PRIVATE_KEY`, `GEMINI_API_KEY`, `DATABASE_URL`).
3. Test running a command with outbound network access (e.g., `curl https://example.com`) inside `SandboxRunner.run()` and verify it fails with non-zero exit code.
