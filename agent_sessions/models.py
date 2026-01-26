"""Unified session model for all providers."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class Session:
    """Unified session model for all AI coding harnesses."""

    # Identity
    id: str
    harness: str  # provider name: "droid", "claude-code", etc.
    raw_path: Path  # original file location

    # Project context
    project_path: Path
    project_name: str

    # Content
    title: str = ""
    first_prompt: str = ""
    last_prompt: str = ""
    last_response: str = ""

    # Timing
    created_time: Optional[datetime] = None
    modified_time: Optional[datetime] = None

    # Hierarchy
    is_child: bool = False
    child_type: str = ""  # e.g., "debugger", "code-reviewer"
    parent_id: Optional[str] = None

    # Metadata
    model: str = ""
    tool_calls: list[str] = field(default_factory=list)
    tokens_used: Optional[int] = None

    # Computed (cached)
    summary: Optional[str] = None
    content_hash: str = ""

    # Provider-specific data
    extra: dict = field(default_factory=dict)


@dataclass
class SearchResult:
    """A single search result with context."""

    session: Session
    role: str  # "user" or "assistant"
    match_text: str
    context_before: list[str]
    context_after: list[str]
    line_num: int

    @property
    def session_id(self) -> str:
        return self.session.id
