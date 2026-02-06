"""Base class for session providers."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Session


def detect_automated_session(first_prompt: str) -> tuple[bool, str]:
    """Detect if a session is system-generated/automated rather than human-initiated.
    
    These are sessions started by tools, CI bots, system commands, or context injections
    rather than a human typing a prompt. Shared across all providers.
    
    Returns (is_automated, automation_type) tuple.
    """
    if not first_prompt or not first_prompt.strip():
        return False, ""
    
    prompt_start = first_prompt[:500].strip()
    prompt_lower = prompt_start.lower()

    # XML-tagged system content
    if prompt_start.startswith("<system-notification>"):
        return True, "system-notification"
    if prompt_start.startswith("<command-message>"):
        return True, "command-message"
    if prompt_start.startswith("<command-instruction>"):
        return True, "command-instruction"
    if prompt_start.startswith("<local-command-caveat>"):
        return True, "command-caveat"
    if prompt_start.startswith("<ultrawork-mode>"):
        return True, "ultrawork-mode"

    # Bracketed system directives
    if prompt_start.startswith("[search-mode]"):
        return True, "search-mode"
    if prompt_start.startswith("[analyze-mode]"):
        return True, "analyze-mode"
    if prompt_start.startswith("[SYSTEM DIRECTIVE"):
        return True, "system-directive"
    if prompt_start.startswith("[COMPACTION CONTEXT"):
        return True, "compaction-context"
    if prompt_start.startswith("[GAS TOWN]") or prompt_start.startswith("[gas town]"):
        return True, "ci-dispatch"

    # Bot/CI dispatches
    if "polecat dispatched" in prompt_lower:
        return True, "ci-dispatch"
    if prompt_lower.startswith("gt boot") or prompt_lower.startswith("gt prime") or prompt_lower.startswith("gt hook"):
        return True, "ci-dispatch"
    if prompt_lower.startswith("run `gt hook`") or prompt_lower.startswith("run `gt boot`"):
        return True, "ci-dispatch"

    # Sub-agent continuation prompts
    if prompt_lower.startswith("summarize the task tool output above"):
        return True, "subagent-continuation"

    return False, ""


class SessionProvider(ABC):
    """Abstract base class for session providers.

    Each AI coding harness (Droid, Claude Code, Cursor, etc.) implements
    this interface to provide session discovery and parsing.
    """

    # Provider identity
    name: str = ""  # unique identifier: "droid", "claude-code", etc.
    display_name: str = ""  # human-readable: "Factory Droid", "Claude Code"
    icon: str = ""  # emoji for UI: "🤖", "🧠"
    color: str = ""  # for UI theming

    # Whether discover_session_files() is cheap enough for startup auto-index.
    # Providers with virtual paths or DB queries should set this to False.
    fast_discovery: bool = True

    @abstractmethod
    def get_sessions_dir(self) -> Path:
        """Return the directory where sessions are stored."""
        ...

    def is_available(self) -> bool:
        """Check if this provider's sessions directory exists."""
        return self.get_sessions_dir().exists()

    @abstractmethod
    def discover_session_files(self) -> list[Path]:
        """Discover all session files in the sessions directory."""
        ...

    @abstractmethod
    def parse_session(self, path: Path) -> Session | None:
        """Parse a session file into a Session object."""
        ...

    def load_sessions(self) -> list[Session]:
        """Load all sessions from this provider."""
        sessions = []
        for path in self.discover_session_files():
            try:
                session = self.parse_session(path)
                if session:
                    sessions.append(session)
            except Exception:
                continue
        return sessions

    @abstractmethod
    def get_resume_command(self, session: Session) -> str:
        """Get the command to resume a session."""
        ...

    def find_children(self, parent: Session, all_sessions: list[Session]) -> list[Session]:
        """Find child/sub-agent sessions related to a parent session.

        Default implementation returns empty list. Override for harnesses
        that support hierarchical sessions.
        """
        return []

    def get_task_invocations(self, session: Session) -> list[dict]:
        """Get Task tool invocations from a session.

        Returns list of dicts with keys: subagent_type, timestamp, description.
        Default implementation returns empty list.
        """
        return []

    def discover_sessions_fast(self) -> dict[str, int]:
        """Discover sessions with minimal parsing - just IDs and mtimes.
        
        Returns dict mapping session_id to file mtime (as int timestamp).
        Used for incremental indexing to detect changed sessions.
        """
        result = {}
        for path in self.discover_session_files():
            try:
                session_id = path.stem
                mtime = int(path.stat().st_mtime)
                result[session_id] = mtime
            except OSError:
                continue
        return result

    def get_session_messages(self, session: Session) -> list[dict]:
        """Get all messages from a session for indexing.
        
        Returns list of dicts with keys: id, role, content, timestamp (optional).
        Default implementation returns empty list - override in subclasses.
        """
        return []
