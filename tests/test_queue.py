"""Tests for job queue deduplication and stale-commit cancellation."""

from pr_agent.queue import InMemoryJobQueue, JobStatus, ReviewJob


def _make_job(sha: str) -> ReviewJob:
    return ReviewJob(
        repo_id=1001,
        repo_full_name="acme/backend",
        pr_number=42,
        head_sha=sha,
        installation_id=99,
        trigger_event="pull_request.synchronize",
    )


async def test_duplicate_commit_is_not_requeued() -> None:
    queue = InMemoryJobQueue()
    assert await queue.enqueue(_make_job("sha_1")) is True
    assert await queue.enqueue(_make_job("sha_1")) is False


async def test_newer_commit_cancels_stale_job() -> None:
    queue = InMemoryJobQueue()
    job1 = _make_job("sha_1")
    job2 = _make_job("sha_2")

    await queue.enqueue(job1)
    await queue.enqueue(job2)

    assert await queue.is_cancelled(job1.job_key) is True
    assert await queue.is_cancelled(job2.job_key) is False

    # Dequeue skips cancelled job1 and returns job2.
    next_job = await queue.dequeue()
    assert next_job is not None
    assert next_job.head_sha == "sha_2"
    assert next_job.status == JobStatus.RUNNING

