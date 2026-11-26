"""FastAPI application factory."""

from fastapi import FastAPI

from pr_agent.config import Settings, get_settings
from pr_agent.queue import JobQueue, create_job_queue
from pr_agent.webhook.router import create_webhook_router


def create_app(
    settings: Settings | None = None,
    job_queue: JobQueue | None = None,
) -> FastAPI:
    """Create and configure the FastAPI webhook application."""
    cfg = settings or get_settings()
    queue = job_queue or create_job_queue(cfg.queue_backend, cfg.redis_url)

    app = FastAPI(
        title="Autonomous PR Review Agent",
        version="0.1.0",
    )
    app.state.job_queue = queue
    app.include_router(
        create_webhook_router(
            webhook_secret=cfg.github_webhook_secret,
            job_queue=queue,
            trigger_command=cfg.review_trigger_command,
        )
    )
    return app

