# System Architecture & High-Level Design (HLD)

## Core Pattern: Orchestrator–Workers (Map–Reduce)

Rather than using a single monolithic LLM loop or an unconstrained multi-agent chat swarm, the system uses a **code-controlled Orchestrator** that dispatches **isolated, parallel specialist subagents** and routes their outputs to a **single Judge**:

1. **Your Backend Code Owns Control Flow:** Webhooks, queueing, stale-job cancellation, subagent dispatch, timeouts, and GitHub API writes are all deterministic code.
2. **Subagents Are Isolated & Read-Only:** Specialists never communicate with each other and never write to GitHub. They investigate the codebase using bounded read-only tools and return structured JSON findings.
3. **Single Quality Gate:** Every candidate finding is verified and deduplicated by a single Judge before being mapped to GitHub diff hunks and published.

---

## High-Level Design Diagram

```mermaid
flowchart TD
    GH["GitHub (PR Opened / Synchronize / /review)"] -->|"Signed Webhook (X-Hub-Signature-256)"| GW["Webhook Gateway (Verify & Return 202)"]
    GW -->|"Enqueue (repo, pr, head_sha)"| Q["Job Queue (Redis / BullMQ / Temporal - Cancel Stale Runs)"]

    subgraph ORCH["Orchestrator Worker (Backend Code)"]
        direction TB
        PREP["1. Diff & Config Prep (Strip lockfiles, load .pr-agent.yaml)"]
        STAT["2. Deterministic Pre-Checks (Semgrep, AST, Linters)"]
        R1["3a. Tier-1 Router (Deterministic Path & Risk Rules)"]
        R2["3b. Tier-2 Router (Fast LLM Triage for Ambiguous PRs)"]
        PREP --> STAT --> R1 --> R2
    end

    Q --> PREP

    subgraph SUBS["4. Parallel Specialist Subagents (Code-Invoked, Max Steps & Timeout)"]
        direction LR
        S1["Logic & Bugs Agent"]
        S2["Security Agent"]
        S3["Reliability / Perf Agent"]
        S4["DB / Test Agent"]
    end

    TOOLS["Read-Only Codebase Tools (read_file, ripgrep search_code, AST/symbols) in Isolated Sandbox"]
    R2 -->|"Spawn relevant specialists"| SUBS
    S1 <-->|"Tool calls"| TOOLS
    S2 <-->|"Tool calls"| TOOLS
    S3 <-->|"Tool calls"| TOOLS
    S4 <-->|"Tool calls"| TOOLS

    subgraph SYNTH["5. Synthesis & Publishing"]
        direction TB
        JUDGE["Single Judge Agent (Verify claims, deduplicate, drop nitpicks)"]
        MAP["Diff Line Mapper (Validate file + line in diff hunk)"]
        PUB["GitHub Publisher (Post Inline Review + Check Run)"]
        JUDGE --> MAP --> PUB
    end

    SUBS -->|"Structured JSON Findings"| JUDGE
    JUDGE <-->|"Verify claims"| TOOLS

    DB[("PostgreSQL (PR state, posted comment IDs, dedup)")]
    OBS["Observability & Evals (Langfuse / OTel / Feedback Reactions)"]

    ORCH <--> DB
    SYNTH <--> DB
    PUB -->|"PR Review API + Check Runs API"| GH
    SYNTH --> OBS
    GH -->|"Reactions & Resolved Threads"| OBS
```

---

## Component Responsibilities

### 1. Webhook Gateway
- Validates `X-Hub-Signature-256` on every incoming GitHub webhook.
- Filters out irrelevant events (e.g., draft PRs if disabled by repo config).
- Returns `202 Accepted` immediately (within GitHub's ~10s timeout) and enqueues a job keyed by `(repo_id, pr_number, head_sha)`.

### 2. Job Queue & Concurrency Control
- Handles retries with backoff and deduplicates repeated webhook deliveries.
- Supersedes or cancels in-flight review jobs when a newer commit (`synchronize`) arrives for the same PR.

### 3. Orchestrator Worker
- **Diff & Config Prep:** Clones/fetches the PR branch into an isolated workspace, strips noisy generated files (`package-lock.json`, `dist/`, `*.min.js`), and loads `.pr-agent.yaml` / `AGENTS.md`.
- **Deterministic Pre-Checks:** Runs fast static analyzers (e.g., Semgrep, linters, typecheckers) so LLMs don't waste tokens rediscovering deterministic issues.
- **Two-Tier Router:**
  - *Tier 1 (Deterministic Rules):* Maps file paths and metadata to specialists (e.g., `*.sql` / `migrations/` → DB Agent; `auth/` / routes → Security Agent; docs-only → skip).
  - *Tier 2 (Lightweight LLM Triage):* For ambiguous changes, a fast/cheap model inspects the PR title, description, and changed symbols and outputs a JSON list of specialists to invoke.

### 4. Parallel Specialist Subagents
- Invoked concurrently by orchestrator code (`asyncio.gather` / `Promise.all`) with strict step caps (e.g., 8–10 tool turns) and timeouts (e.g., 45s).
- Equipped with strictly **read-only** codebase tools (`read_file`, `search_code`, `get_symbol_definition`).
- Output strict schema-validated JSON findings: `{ file, line, side, severity, confidence, title, explanation, suggested_fix }`.

### 5. Single Judge & Publisher
- **Judge:** Reviews all candidate findings, verifies claims against the actual code if needed, merges/deduplicates overlapping findings, drops low-confidence style nitpicks, and checks against previously dismissed threads.
- **Diff Line Mapper:** Ensures every inline comment maps to a valid `(file, line, side)` inside a GitHub diff hunk (preventing `422 Unprocessable Entity` errors), falling back to the summary body when needed.
- **GitHub Publisher:** Submits a single clean Pull Request Review and updates the associated GitHub Check Run.

