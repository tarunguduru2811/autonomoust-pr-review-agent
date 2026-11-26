"""Diff preparation, noise filtering, and repository rule loading."""

from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel

_IGNORED_PATTERNS = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "Cargo.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "dist/*",
    "build/*",
    "vendor/*",
    "node_modules/*",
)


class FileDiffSummary(BaseModel):
    """Cleaned per-file diff extracted from a unified diff."""

    path: str
    patch: str
    added_lines: int
    removed_lines: int


class PreparedDiff(BaseModel):
    """Result of filtering noisy files and summarizing a PR diff."""

    cleaned_diff: str
    files: list[FileDiffSummary]
    skipped_files: list[str]


def is_ignored_file(path: str, extra_ignore_patterns: list[str] | None = None) -> bool:
    """Return True if a file path matches lockfile, build artifact, or repo ignore rules."""
    normalized = path.lstrip("./")
    patterns = list(_IGNORED_PATTERNS) + (extra_ignore_patterns or [])
    for pattern in patterns:
        if fnmatch(normalized, pattern) or fnmatch(Path(normalized).name, pattern):
            return True
    return False


def prepare_diff(
    unified_diff: str,
    extra_ignore_patterns: list[str] | None = None,
) -> PreparedDiff:
    """Split a unified diff by file, drop noisy/generated files, and rebuild cleaned diff."""
    sections = _split_diff_sections(unified_diff)
    kept_files: list[FileDiffSummary] = []
    kept_patches: list[str] = []
    skipped: list[str] = []

    for path, patch in sections:
        if is_ignored_file(path, extra_ignore_patterns):
            skipped.append(path)
            continue

        added = 0
        removed = 0
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++ "):
                added += 1
            elif line.startswith("-") and not line.startswith("--- "):
                removed += 1

        kept_files.append(
            FileDiffSummary(
                path=path,
                patch=patch,
                added_lines=added,
                removed_lines=removed,
            )
        )
        kept_patches.append(patch)

    return PreparedDiff(
        cleaned_diff="\n".join(kept_patches),
        files=kept_files,
        skipped_files=skipped,
    )


def _split_diff_sections(unified_diff: str) -> list[tuple[str, str]]:
    """Split a multi-file unified diff into (file_path, section_text) pairs."""
    sections: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_path: str | None = None

    for line in unified_diff.splitlines():
        if line.startswith("diff --git "):
            if current_lines and current_path:
                sections.append((current_path, "\n".join(current_lines)))
            current_lines = [line]
            parts = line.split(" b/", maxsplit=1)
            current_path = parts[1] if len(parts) == 2 else None
        else:
            current_lines.append(line)
            if line.startswith("+++ b/") and current_path is None:
                current_path = line[6:]

    if current_lines and current_path:
        sections.append((current_path, "\n".join(current_lines)))

    return sections


def load_repo_rules(workspace_root: Path) -> str:
    """Load repository review conventions from .pr-agent.yaml and AGENTS.md if present."""
    chunks: list[str] = []
    for filename in (".pr-agent.yaml", ".pr-agent.yml", "AGENTS.md"):
        candidate = workspace_root / filename
        if candidate.is_file():
            content = candidate.read_text(encoding="utf-8", errors="replace")[:8000]
            chunks.append(f"### {filename}\n{content}")
    return "\n\n".join(chunks)
