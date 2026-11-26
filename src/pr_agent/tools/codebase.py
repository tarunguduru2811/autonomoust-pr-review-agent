"""Read-only, bounded codebase inspection tools backed by RunContext caching."""

import asyncio
import re
from pathlib import Path

from pydantic import BaseModel

from pr_agent.orchestrator.run_context import RunContext

MAX_READ_LINES = 150
MAX_SEARCH_MATCHES = 20
MAX_SYMBOL_MATCHES = 10
_MAX_LINE_CHARS = 300


class ReadFileResult(BaseModel):
    """Bounded slice of a repository file."""

    path: str
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    content: str


class SearchMatch(BaseModel):
    """Single ripgrep search match."""

    file: str
    line: int
    text: str


class SearchResult(BaseModel):
    """Bounded search output."""

    query: str
    matches: list[SearchMatch]
    truncated: bool


class CodebaseTools:
    """Provides strictly read-only, bounded tools within a workspace root."""

    def __init__(self, workspace_root: Path, run_context: RunContext | None = None) -> None:
        self._root = workspace_root.resolve()
        self._run_context = run_context

    def _resolve_safe_path(self, rel_path: str) -> Path:
        candidate = (self._root / rel_path).resolve()
        if not candidate.is_relative_to(self._root):
            raise ValueError(f"Access denied: '{rel_path}' escapes workspace root.")
        return candidate

    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> ReadFileResult:
        """Read up to MAX_READ_LINES from a file inside the workspace."""
        start = max(1, start_line)
        requested_end = end_line if end_line is not None else start + MAX_READ_LINES - 1
        capped_end = min(requested_end, start + MAX_READ_LINES - 1)

        cache_key = f"read_file:{path}:{start}:{capped_end}"
        if self._run_context is not None:
            hit, cached = self._run_context.get_cached_tool_result(cache_key)
            if hit:
                return cached

        target = self._resolve_safe_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        all_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(all_lines)
        actual_end = min(capped_end, total)
        slice_lines = all_lines[start - 1 : actual_end]

        formatted = "\n".join(
            f"{start + idx}: {line[:_MAX_LINE_CHARS]}" for idx, line in enumerate(slice_lines)
        )
        result = ReadFileResult(
            path=path,
            start_line=start,
            end_line=actual_end,
            total_lines=total,
            truncated=(requested_end > capped_end) or (actual_end < total),
            content=formatted,
        )

        if self._run_context is not None:
            await self._run_context.set_cached_tool_result(cache_key, result)
        return result

    async def search_code(
        self,
        pattern: str,
        file_glob: str | None = None,
        literal: bool = True,
    ) -> SearchResult:
        """Search workspace using ripgrep, capped at MAX_SEARCH_MATCHES."""
        cache_key = f"search_code:{pattern}:{file_glob or '*'}:{literal}"
        if self._run_context is not None:
            hit, cached = self._run_context.get_cached_tool_result(cache_key)
            if hit:
                return cached

        cmd = ["rg", "--line-number", "--no-heading", "--color", "never"]
        if literal:
            cmd.append("--fixed-strings")
        if file_glob:
            cmd.extend(["--glob", file_glob])
        cmd.extend(["--", pattern, "."])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self._root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)

        matches: list[SearchMatch] = []
        truncated = False
        for raw in stdout.decode("utf-8", errors="replace").splitlines():
            parts = raw.split(":", maxsplit=2)
            if len(parts) != 3:
                continue
            file_path, line_str, text = parts
            if not line_str.isdigit():
                continue
            if len(matches) >= MAX_SEARCH_MATCHES:
                truncated = True
                break
            matches.append(
                SearchMatch(
                    file=file_path.lstrip("./"),
                    line=int(line_str),
                    text=text.strip()[:_MAX_LINE_CHARS],
                )
            )

        result = SearchResult(query=pattern, matches=matches, truncated=truncated)
        if self._run_context is not None:
            await self._run_context.set_cached_tool_result(cache_key, result)
        return result

    async def get_symbol_definition(self, symbol: str) -> SearchResult:
        """Locate likely definitions (def/class/function/type/interface/const) for a symbol."""
        escaped = re.escape(symbol)
        regex = (
            rf"^\s*(?:export\s+)?(?:async\s+)?"
            rf"(?:def|class|function|interface|type|const|let|var|func|struct)\s+{escaped}\b"
        )
        cache_key = f"symbol_def:{symbol}"
        if self._run_context is not None:
            hit, cached = self._run_context.get_cached_tool_result(cache_key)
            if hit:
                return cached

        raw_result = await self.search_code(regex, literal=False)
        capped = SearchResult(
            query=symbol,
            matches=raw_result.matches[:MAX_SYMBOL_MATCHES],
            truncated=len(raw_result.matches) > MAX_SYMBOL_MATCHES or raw_result.truncated,
        )
        if self._run_context is not None:
            await self._run_context.set_cached_tool_result(cache_key, capped)
        return capped
