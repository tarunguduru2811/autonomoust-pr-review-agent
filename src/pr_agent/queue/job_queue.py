"""Job queue implementations with idempotency and stale-run cancellation."""

from collections import deque
from typing import Protocol

from redis.asyncio import Redis

from pr_agent.queue.models import JobStatus, ReviewJob


class JobQueue(Protocol):
    """Interface for PR review job queues."""

    async def enqueue(self, job: ReviewJob) -> bool:
        """Enqueue a job, cancelling older jobs for the same PR.

        Returns True if newly enqueued, False if this exact (repo, pr, head_sha) is already active.
        """
        ...

    async def dequeue(self) -> ReviewJob | None:
        """Pop the next non-cancelled job, or None if the queue is empty."""
        ...

    async def is_cancelled(self, job_key: str) -> bool:
        """Return True if a newer commit superseded this job."""
        ...

    async def mark_status(self, job_key: str, status: JobStatus) -> None:
        """Update the lifecycle status of a job."""
        ...


class InMemoryJobQueue:
    """In-memory job queue for local development and unit tests."""

    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._jobs: dict[str, ReviewJob] = {}
        self._active_by_pr: dict[str, str] = {}

    async def enqueue(self, job: ReviewJob) -> bool:
        existing = self._jobs.get(job.job_key)
        if existing and existing.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            return False

        # Cancel any prior active job for the same PR so stale commits never publish.
        prev_key = self._active_by_pr.get(job.pr_key)
        if prev_key and prev_key != job.job_key and prev_key in self._jobs:
            prev_job = self._jobs[prev_key]
            if prev_job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                prev_job.status = JobStatus.CANCELLED

        self._jobs[job.job_key] = job
        self._active_by_pr[job.pr_key] = job.job_key
        self._queue.append(job.job_key)
        return True

    async def dequeue(self) -> ReviewJob | None:
        while self._queue:
            key = self._queue.popleft()
            job = self._jobs.get(key)
            if job is None or job.status == JobStatus.CANCELLED:
                continue
            job.status = JobStatus.RUNNING
            return job
        return None

    async def is_cancelled(self, job_key: str) -> bool:
        job = self._jobs.get(job_key)
        return job is not None and job.status == JobStatus.CANCELLED

    async def mark_status(self, job_key: str, status: JobStatus) -> None:
        if job_key in self._jobs:
            self._jobs[job_key].status = status

    def get_job(self, job_key: str) -> ReviewJob | None:
        """Return stored job metadata (useful for inspection and tests)."""
        return self._jobs.get(job_key)


class RedisJobQueue:
    """Redis-backed job queue with atomic stale-job cancellation."""

    _QUEUE_KEY = "pr_agent:queue"
    _JOB_PREFIX = "pr_agent:job:"
    _ACTIVE_PR_PREFIX = "pr_agent:active_pr:"
    _TTL_SECONDS = 86400

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def enqueue(self, job: ReviewJob) -> bool:
        job_redis_key = f"{self._JOB_PREFIX}{job.job_key}"
        active_pr_key = f"{self._ACTIVE_PR_PREFIX}{job.pr_key}"

        existing_status = await self._redis.hget(job_redis_key, "status")
        if existing_status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
            return False

        prev_key = await self._redis.get(active_pr_key)
        if prev_key and prev_key != job.job_key:
            await self._redis.hset(
                f"{self._JOB_PREFIX}{prev_key}",
                "status",
                JobStatus.CANCELLED.value,
            )

        await self._redis.hset(
            job_redis_key,
            mapping={"payload": job.model_dump_json(), "status": JobStatus.QUEUED.value},
        )
        await self._redis.expire(job_redis_key, self._TTL_SECONDS)
        await self._redis.set(active_pr_key, job.job_key, ex=self._TTL_SECONDS)
        await self._redis.rpush(self._QUEUE_KEY, job.job_key)
        return True

    async def dequeue(self) -> ReviewJob | None:
        while True:
            key = await self._redis.lpop(self._QUEUE_KEY)
            if key is None:
                return None
            job_redis_key = f"{self._JOB_PREFIX}{key}"
            data = await self._redis.hgetall(job_redis_key)
            if not data or data.get("status") == JobStatus.CANCELLED.value:
                continue
            await self._redis.hset(job_redis_key, "status", JobStatus.RUNNING.value)
            job = ReviewJob.model_validate_json(data["payload"])
            job.status = JobStatus.RUNNING
            return job

    async def is_cancelled(self, job_key: str) -> bool:
        status = await self._redis.hget(f"{self._JOB_PREFIX}{job_key}", "status")
        return status == JobStatus.CANCELLED.value

    async def mark_status(self, job_key: str, status: JobStatus) -> None:
        await self._redis.hset(f"{self._JOB_PREFIX}{job_key}", "status", status.value)


def create_job_queue(backend: str, redis_url: str) -> JobQueue:
    """Factory for creating the configured job queue backend."""
    if backend == "redis":
        client = Redis.from_url(redis_url, decode_responses=True)
        return RedisJobQueue(client)
    return InMemoryJobQueue()
