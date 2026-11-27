"""PR-lifecycle ledger implementations (In-Memory and PostgreSQL/asyncpg)."""

from typing import Any, Protocol

from pr_agent.db.models import CommentStatus, PRReviewRecord, ReviewCommentRecord

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pr_reviews (
    repo_id BIGINT NOT NULL,
    pr_number INTEGER NOT NULL,
    last_reviewed_sha TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo_id, pr_number)
);

CREATE TABLE IF NOT EXISTS review_comments (
    repo_id BIGINT NOT NULL,
    pr_number INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    comment_id BIGINT,
    status TEXT NOT NULL DEFAULT 'open',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (repo_id, pr_number, fingerprint)
);
"""


class PRLedger(Protocol):
    """Interface for storing and querying PR review state across commits."""

    async def upsert_review(self, record: PRReviewRecord) -> None: ...

    async def record_comments(self, comments: list[ReviewCommentRecord]) -> None: ...

    async def get_existing_threads(self, repo_id: int, pr_number: int) -> list[dict[str, Any]]: ...

    async def update_comment_status(
        self,
        repo_id: int,
        pr_number: int,
        fingerprint: str,
        status: CommentStatus,
    ) -> None: ...

    async def mark_resolved_if_absent(
        self,
        repo_id: int,
        pr_number: int,
        active_fingerprints: set[str],
    ) -> None: ...


class InMemoryPRLedger:
    """In-memory PR ledger for local development and unit/integration tests."""

    def __init__(self) -> None:
        self._reviews: dict[tuple[int, int], PRReviewRecord] = {}
        self._comments: dict[tuple[int, int, str], ReviewCommentRecord] = {}

    async def upsert_review(self, record: PRReviewRecord) -> None:
        self._reviews[(record.repo_id, record.pr_number)] = record

    async def record_comments(self, comments: list[ReviewCommentRecord]) -> None:
        for comment in comments:
            key = (comment.repo_id, comment.pr_number, comment.fingerprint)
            existing = self._comments.get(key)
            if existing and existing.status in (CommentStatus.RESOLVED, CommentStatus.DISMISSED):
                continue
            self._comments[key] = comment

    async def get_existing_threads(self, repo_id: int, pr_number: int) -> list[dict[str, Any]]:
        return [
            {
                "fingerprint": c.fingerprint,
                "file": c.file,
                "line": c.line,
                "status": c.status.value,
                "title": c.title,
            }
            for (r_id, p_num, _), c in self._comments.items()
            if r_id == repo_id and p_num == pr_number
        ]

    async def update_comment_status(
        self,
        repo_id: int,
        pr_number: int,
        fingerprint: str,
        status: CommentStatus,
    ) -> None:
        key = (repo_id, pr_number, fingerprint)
        if key in self._comments:
            self._comments[key].status = status

    async def mark_resolved_if_absent(
        self,
        repo_id: int,
        pr_number: int,
        active_fingerprints: set[str],
    ) -> None:
        for (r_id, p_num, fp), comment in self._comments.items():
            if (
                r_id == repo_id
                and p_num == pr_number
                and comment.status == CommentStatus.OPEN
                and fp not in active_fingerprints
            ):
                comment.status = CommentStatus.FIXED


class PostgresPRLedger:
    """PostgreSQL-backed PR ledger using an asyncpg connection pool."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create ledger tables if they do not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)

    async def upsert_review(self, record: PRReviewRecord) -> None:
        sql = """
        INSERT INTO pr_reviews (repo_id, pr_number, last_reviewed_sha, updated_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (repo_id, pr_number)
        DO UPDATE SET last_reviewed_sha = EXCLUDED.last_reviewed_sha,
                      updated_at = EXCLUDED.updated_at;
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                sql,
                record.repo_id,
                record.pr_number,
                record.last_reviewed_sha,
                record.updated_at,
            )

    async def record_comments(self, comments: list[ReviewCommentRecord]) -> None:
        if not comments:
            return
        sql = """
        INSERT INTO review_comments (
            repo_id, pr_number, head_sha, fingerprint, file, line,
            category, title, comment_id, status, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (repo_id, pr_number, fingerprint)
        DO UPDATE SET head_sha = EXCLUDED.head_sha,
                      line = EXCLUDED.line,
                      updated_at = EXCLUDED.updated_at
        WHERE review_comments.status NOT IN ('resolved', 'dismissed');
        """
        async with self._pool.acquire() as conn:
            for c in comments:
                await conn.execute(
                    sql,
                    c.repo_id,
                    c.pr_number,
                    c.head_sha,
                    c.fingerprint,
                    c.file,
                    c.line,
                    c.category,
                    c.title,
                    c.comment_id,
                    c.status.value,
                    c.updated_at,
                )

    async def get_existing_threads(self, repo_id: int, pr_number: int) -> list[dict[str, Any]]:
        sql = """
        SELECT fingerprint, file, line, status, title
        FROM review_comments
        WHERE repo_id = $1 AND pr_number = $2;
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, repo_id, pr_number)
        return [dict(r) for r in rows]

    async def update_comment_status(
        self,
        repo_id: int,
        pr_number: int,
        fingerprint: str,
        status: CommentStatus,
    ) -> None:
        sql = """
        UPDATE review_comments
        SET status = $4, updated_at = NOW()
        WHERE repo_id = $1 AND pr_number = $2 AND fingerprint = $3;
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, repo_id, pr_number, fingerprint, status.value)

    async def mark_resolved_if_absent(
        self,
        repo_id: int,
        pr_number: int,
        active_fingerprints: set[str],
    ) -> None:
        sql = """
        UPDATE review_comments
        SET status = 'fixed', updated_at = NOW()
        WHERE repo_id = $1 AND pr_number = $2
          AND status = 'open'
          AND NOT (fingerprint = ANY($3::text[]));
        """
        async with self._pool.acquire() as conn:
            await conn.execute(sql, repo_id, pr_number, list(active_fingerprints))

