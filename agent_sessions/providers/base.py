"""Base class for session providers."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Session


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
