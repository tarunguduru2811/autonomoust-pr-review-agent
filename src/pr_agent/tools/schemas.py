"""Canonical JSON schemas for read-only codebase tools passed to LLM APIs."""

from typing import Any

from pydantic import BaseModel, Field


class ReadFileArgs(BaseModel):
    """Arguments for reading a bounded slice of a repository file."""

    path: str = Field(description="Relative file path from repository root.")
    start_line: int = Field(default=1, ge=1, description="1-based start line.")
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="1-based inclusive end line (capped at 150 lines from start_line).",
    )


class SearchCodeArgs(BaseModel):
    """Arguments for searching the repository using ripgrep."""

    pattern: str = Field(description="Literal text or pattern to search for.")
    file_glob: str | None = Field(
        default=None,
        description="Optional glob filter, e.g. '*.py' or 'src/**'.",
    )


class GetSymbolDefinitionArgs(BaseModel):
    """Arguments for locating symbol definitions in the repository."""

    symbol: str = Field(description="Function, class, interface, type, or constant name.")


READ_ONLY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read up to 150 lines from a file inside the PR workspace.",
        "parameters": ReadFileArgs.model_json_schema(),
    },
    {
        "name": "search_code",
        "description": "Search repository code using ripgrep (capped at 20 matches).",
        "parameters": SearchCodeArgs.model_json_schema(),
    },
    {
        "name": "get_symbol_definition",
        "description": "Find likely definitions of a symbol across the repository.",
        "parameters": GetSymbolDefinitionArgs.model_json_schema(),
    },
]
