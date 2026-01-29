"""OpenCode session provider."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..cache import MetadataCache, SummaryCache, compute_content_hash
from ..models import Session
from . import register_provider
from .base import SessionProvider


# OpenCode stores data in XDG-style directories
OPENCODE_STATE_DIR = Path.home() / ".local" / "state" / "opencode"
OPENCODE_DATA_DIR = Path.home() / ".local" / "share" / "opencode"
STORAGE_DIR = OPENCODE_DATA_DIR / "storage"
MESSAGE_DIR = STORAGE_DIR / "message"
PART_DIR = STORAGE_DIR / "part"
SESSION_DIR = STORAGE_DIR / "session"


def _detect_subagent(first_prompt: str) -> tuple[bool, str]:
    """Detect if a session is a sub-agent/single-task based on prompt patterns.

    Returns (is_child, child_type) tuple.
    """
    if not first_prompt:
        return False, ""

    # Check first 500 chars for efficiency
    prompt_start = first_prompt[:500]
    prompt_lower = prompt_start.lower()

    # System reminder/directive patterns
    if "<system-reminder>" in prompt_start:
        return True, "system-task"
    if "<system_reminder>" in prompt_start:
        return True, "system-task"
    if "[system directive:" in prompt_lower:
        return True, "system-task"

    # Single task patterns
    if "single task only" in prompt_lower:
        return True, "single-task"
    if "single-task" in prompt_lower:
        return True, "single-task"

    # File analysis patterns
    if "analyze this file" in prompt_lower:
        return True, "file-analysis"
    if "extract the requested information" in prompt_lower:
        return True, "file-analysis"

    # Tool/agent patterns
    if prompt_start.startswith("Tool:") or prompt_start.startswith("tool:"):
        return True, "tool-call"

    return False, ""


@register_provider
class OpenCodeProvider(SessionProvider):
    """Provider for OpenCode sessions."""

    name = "opencode"
    display_name = "OpenCode"
    icon = "💻"
    color = "magenta"

    def get_sessions_dir(self) -> Path:
        return OPENCODE_DATA_DIR

    def discover_session_files(self) -> list[Path]:
        """Discover all session directories (each has message subdirs)."""
        if not MESSAGE_DIR.exists():
            return []

        # Each subdirectory in message/ is a session
        files = []
        for session_dir in MESSAGE_DIR.iterdir():
            if session_dir.is_dir() and session_dir.name.startswith("ses_"):
                # Use a virtual path for each session
                virtual_path = STORAGE_DIR / "sessions" / f"{session_dir.name}.opencode"
                files.append(virtual_path)

        return files

    def _session_from_cache(self, path: Path, cached: dict) -> Session:
        """Construct Session from cached metadata."""
        created_time = None
        if cached.get("created_time"):
            try:
                created_time = datetime.fromisoformat(cached["created_time"])
            except (ValueError, TypeError):
                pass

        modified_time = None
        if cached.get("modified_time"):
            try:
                modified_time = datetime.fromisoformat(cached["modified_time"])
            except (ValueError, TypeError):
                pass
        if not modified_time:
            modified_time = created_time or datetime.now()

        content_hash = cached.get("content_hash", "")
        summary = None
        if cached.get("first_prompt"):
            summary_cache = SummaryCache()
            summary = summary_cache.get(path.stem, content_hash)

        return Session(
            id=cached.get("session_id", path.stem),
            harness=self.name,
            raw_path=path,
            project_path=Path(cached.get("project_path", "")),
            project_name=cached.get("project_name", ""),
            title=cached.get("title", ""),
            first_prompt=cached.get("first_prompt", ""),
            last_prompt=cached.get("last_prompt", ""),
            last_response=cached.get("last_response", ""),
            created_time=created_time,
            modified_time=modified_time,
            is_child=cached.get("is_child", False),
            child_type=cached.get("child_type", ""),
            model=cached.get("model", "unknown"),
            summary=summary,
            content_hash=content_hash,
            extra=cached.get("extra", {}),
        )

    def parse_session(self, path: Path) -> Session | None:
        """Parse an OpenCode session from message/part files."""
        session_id = path.stem  # e.g., "ses_xxx"

        # Get mtime from message directory
        message_session_dir = MESSAGE_DIR / session_id
        if not message_session_dir.exists():
            return None

        try:
            # Use the newest message file's mtime
            message_files = list(message_session_dir.glob("*.json"))
            if not message_files:
                return None
            mtime = max(f.stat().st_mtime for f in message_files)
        except OSError:
            return None

        # Check cache
        cache = MetadataCache()
        cached = cache.get(path, mtime)
        if cached:
            return self._session_from_cache(path, cached)

        # Parse messages to build session data
        messages = []
        project_path = Path.home()
        project_name = "OpenCode"
        model = "unknown"
        agent = ""
        created_time = None
        modified_time = None

        for msg_file in sorted(message_files, key=lambda f: f.name):
            try:
                with open(msg_file) as f:
                    msg = json.load(f)

                role = msg.get("role", "")
                time_data = msg.get("time", {})
                created_ts = time_data.get("created")
                completed_ts = time_data.get("completed")

                # Track first and last timestamps
                if created_ts:
                    ts = datetime.fromtimestamp(created_ts / 1000)
                    if created_time is None or ts < created_time:
                        created_time = ts
                if completed_ts:
                    ts = datetime.fromtimestamp(completed_ts / 1000)
                    if modified_time is None or ts > modified_time:
                        modified_time = ts

                # Get project path from first message
                path_data = msg.get("path", {})
                if path_data.get("root"):
                    project_path = Path(path_data["root"])
                    project_name = project_path.name
                elif path_data.get("cwd"):
                    project_path = Path(path_data["cwd"])
                    project_name = project_path.name

                # Get model/agent from assistant messages
                if role == "assistant":
                    if msg.get("modelID"):
                        model = msg["modelID"]
                    if msg.get("agent"):
                        agent = msg["agent"]

                # Get message content from parts
                msg_id = msg.get("id", "")
                content = self._get_message_content(msg_id)
                if content:
                    messages.append((role, content))

            except (json.JSONDecodeError, IOError, KeyError):
                continue

        if not messages:
            return None

        # Extract first user prompt and last response
        user_messages = [(r, c) for r, c in messages if r == "user"]
        assistant_messages = [(r, c) for r, c in messages if r == "assistant"]

        first_prompt = user_messages[0][1] if user_messages else ""
        last_prompt = user_messages[-1][1] if user_messages else ""
        last_response = assistant_messages[-1][1] if assistant_messages else ""

        # Detect sub-agent sessions
        is_child, child_type = _detect_subagent(first_prompt)

        # Generate title from first prompt
        if first_prompt:
            first_line = first_prompt.split('\n')[0].strip()
            # Skip system tags for title
            if first_line.startswith("<") and ">" in first_line:
                # Try next line if first is a tag
                lines = first_prompt.split('\n')
                for line in lines[1:5]:
                    line = line.strip()
                    if line and not line.startswith("<"):
                        first_line = line
                        break
            title = first_line[:80] if first_line else "OpenCode Session"
        else:
            title = "OpenCode Session"

        # Compute content hash
        content_hash = compute_content_hash(first_prompt, last_response)

        # Get cached summary
        summary = None
        if first_prompt:
            summary_cache = SummaryCache()
            summary = summary_cache.get(session_id, content_hash)

        # Cache metadata
        metadata = {
            "session_id": session_id,
            "project_path": str(project_path),
            "project_name": project_name,
            "title": title,
            "first_prompt": first_prompt[:2000],
            "last_prompt": last_prompt[:2000],
            "last_response": last_response[:2000],
            "created_time": created_time.isoformat() if created_time else None,
            "modified_time": modified_time.isoformat() if modified_time else None,
            "is_child": is_child,
            "child_type": child_type,
            "model": model,
            "content_hash": content_hash,
            "extra": {"agent": agent},
        }
        cache.set(path, mtime, metadata)

        return Session(
            id=session_id,
            harness=self.name,
            raw_path=path,
            project_path=project_path,
            project_name=project_name,
            title=title,
            first_prompt=first_prompt,
            last_prompt=last_prompt,
            last_response=last_response,
            created_time=created_time,
            modified_time=modified_time,
            is_child=is_child,
            child_type=child_type,
            model=model,
            summary=summary,
            content_hash=content_hash,
            extra={"agent": agent},
        )

    def _get_message_content(self, message_id: str) -> str:
        """Get message content from part files."""
        if not message_id:
            return ""

        # Parts are stored in part/{message_id}/ directory
        part_msg_dir = PART_DIR / message_id
        if not part_msg_dir.exists():
            return ""

        texts = []
        for part_file in sorted(part_msg_dir.glob("*.json")):
            try:
                with open(part_file) as f:
                    part = json.load(f)
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            except (json.JSONDecodeError, IOError):
                continue

        return "\n".join(texts)

    def get_resume_command(self, session: Session) -> str:
        return f"opencode --resume {session.id}"

    def find_children(self, parent: Session, all_sessions: list[Session]) -> list[Session]:
        # OpenCode doesn't have a sub-agent concept currently
        return []
