# Autonomous PR Review Agent — Documentation

Welcome to the architecture and design documentation for the **Autonomous GitHub PR Review Agent**.

This project is designed as a production-grade, event-driven PR review system using the **Orchestrator–Workers (Map–Reduce)** multi-agent pattern: deterministic infrastructure controls the pipeline, parallel read-only specialist subagents investigate code changes, and a single Judge verifies and filters findings before posting to GitHub.

## Documentation Index

| Document | Description |
| :--- | :--- |
| [Architecture & HLD](architecture.md) | System components, Orchestrator–Workers pattern, and High-Level Design diagram. |
| [End-to-End Pipeline Flow](pipeline-flow.md) | Step-by-step lifecycle of a PR review from webhook trigger to GitHub Review & Check Run, including a concrete example. |
| [Memory & Context Management](memory-and-context.md) | Working memory, shared `RunContext`, PR-lifecycle deduplication, and repo-level conventions. |
| [Tech Stack, Security & Operations](tech-stack-and-security.md) | Recommended tooling, sandboxing, GitHub App security, rate-limit handling, and observability. |
| [Implementation Plan](implementation-plan.md) | 5-phase build roadmap, target folder structure, and verification steps. |

