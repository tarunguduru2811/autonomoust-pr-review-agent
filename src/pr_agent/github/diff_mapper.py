"""Deterministic Diff Line Mapper for GitHub Pull Request Review inline comments."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
_DIFF_OLD_FILE_RE = re.compile(r"^--- a/(.+)$")


class MappedLocation(BaseModel):
    """Result of mapping a finding location against PR diff hunks."""

    file: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    in_diff_hunk: bool
    fallback_to_summary: bool = False


@dataclass
class _FileHunkLines:
    right_lines: set[int]
    left_lines: set[int]


class DiffLineMapper:
    """Parses unified diffs and validates (file, line, side) for GitHub's Review API."""

    def __init__(self, unified_diff: str) -> None:
        self._files: dict[str, _FileHunkLines] = {}
        self._parse_diff(unified_diff)

    def _parse_diff(self, unified_diff: str) -> None:
        current_file: str | None = None
        old_line = 0
        new_line = 0
        in_hunk = False

        for raw_line in unified_diff.splitlines():
            if raw_line.startswith("diff --git "):
                current_file = None
                in_hunk = False
                continue

            if raw_line.startswith("--- "):
                old_match = _DIFF_OLD_FILE_RE.match(raw_line)
                if old_match and current_file is None:
                    current_file = old_match.group(1)
                continue

            if raw_line.startswith("+++ "):
                new_match = _DIFF_FILE_RE.match(raw_line)
                if new_match:
                    current_file = new_match.group(1)
                if current_file and current_file not in self._files:
                    self._files[current_file] = _FileHunkLines(
                        right_lines=set(),
                        left_lines=set(),
                    )
                in_hunk = False
                continue

            hunk_match = _HUNK_HEADER_RE.match(raw_line)
            if hunk_match and current_file:
                old_line = int(hunk_match.group(1))
                new_line = int(hunk_match.group(3))
                in_hunk = True
                continue

            if not in_hunk or not current_file:
                continue

            entry = self._files[current_file]
            if raw_line.startswith("+"):
                entry.right_lines.add(new_line)
                new_line += 1
            elif raw_line.startswith("-"):
                entry.left_lines.add(old_line)
                old_line += 1
            elif raw_line.startswith(" ") or raw_line == "":
                entry.right_lines.add(new_line)
                entry.left_lines.add(old_line)
                old_line += 1
                new_line += 1

    def is_valid_line(
        self,
        file: str,
        line: int,
        side: Literal["LEFT", "RIGHT"] = "RIGHT",
    ) -> bool:
        """Return True if (file, line, side) falls inside a parsed diff hunk."""
        entry = self._files.get(file)
        if entry is None:
            return False
        if side == "LEFT":
            return line in entry.left_lines
        return line in entry.right_lines

    def map_location(
        self,
        file: str,
        line: int,
        side: Literal["LEFT", "RIGHT"] = "RIGHT",
    ) -> MappedLocation:
        """Validate (file, line, side) or mark for summary fallback."""
        valid = self.is_valid_line(file, line, side)
        return MappedLocation(
            file=file,
            line=line,
            side=side,
            in_diff_hunk=valid,
            fallback_to_summary=not valid,
        )

    @property
    def files(self) -> list[str]:
        """Return all file paths present in the parsed diff."""
        return list(self._files.keys())

