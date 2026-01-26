"""Factory Droid session provider."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..cache import SummaryCache, compute_content_hash
from ..models import Session
from . import register_provider
from .base import SessionProvider


SESSIONS_DIR = Path.home() / ".factory" / "sessions"
SUBAGENT_TITLE_PREFIX = "# Task Tool Invocation"


def decode_path(encoded: str) -> str:
    """Decode directory name back to original path."""
    return encoded.replace("-", "/")


def truncate(text: str, max_len: int = 100) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def extract_text_content(content, text_only: bool = False) -> str:
    """Extract text from message content (handles both string and list formats)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    if text and not text.strip().startswith("<system-reminder>"):
                        texts.append(text)
                elif item.get("type") == "tool_result" and not text_only:
                    texts.append(f"(tool_result: {truncate(str(item.get('content', '')), 50)})")
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts)
    return str(content)


@register_provider
class DroidProvider(SessionProvider):
    """Provider for Factory Droid sessions."""

    name = "droid"
    display_name = "Factory Droid"
    icon = "🤖"
    color = "green"

    def get_sessions_dir(self) -> Path:
        return SESSIONS_DIR

    def discover_session_files(self) -> list[Path]:
        """Discover all JSONL session files."""
        files = []
        sessions_dir = self.get_sessions_dir()
        if not sessions_dir.exists():
            return files

        for project_dir in sessions_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.glob("*.jsonl"):
                files.append(jsonl_file)

        return files

    def parse_session(self, path: Path) -> Session | None:
        """Parse a Droid JSONL session file."""
        settings_path = path.with_suffix(".settings.json")
        project_dir = path.parent.name

        # Defaults
        model = "unknown"
        cwd = ""
        title = "Untitled Session"
        first_user_prompt = ""
        last_user_prompt = ""
        last_assistant_response = ""
        created_time: Optional[datetime] = None
        is_subagent = False
        subagent_type = ""

        # Load settings
        if settings_path.exists():
            try:
                with open(settings_path) as f:
                    settings = json.load(f)
                    model = settings.get("model", "unknown")
            except (json.JSONDecodeError, IOError):
                pass

        # Parse JSONL
        try:
            messages = []
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "session_start":
                            title = data.get("title", data.get("sessionTitle", "Untitled"))[:80]
                            cwd = data.get("cwd", decode_path(project_dir))

                            # Detect sub-agent sessions
                            if title.startswith(SUBAGENT_TITLE_PREFIX):
                                is_subagent = True
                                match = re.search(r'Subagent type: ([a-zA-Z0-9_-]+)', title)
                                if match:
                                    subagent_type = match.group(1)

                        elif data.get("type") == "message":
                            msg = data.get("message", {})
                            role = msg.get("role")

                            # Capture timestamp for first message
                            if created_time is None:
                                ts = data.get("timestamp")
                                if ts:
                                    try:
                                        created_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                    except (ValueError, TypeError):
                                        pass

                            text_only = (role == "user")
                            content = extract_text_content(msg.get("content", ""), text_only=text_only)
                            if role in ("user", "assistant") and content:
                                if "<system-reminder>" in content[:100]:
                                    continue
                                messages.append((role, content))
                    except json.JSONDecodeError:
                        continue

            user_messages = [(r, c) for r, c in messages if r == "user"]
            assistant_messages = [(r, c) for r, c in messages if r == "assistant"]

            if user_messages:
                first_user_prompt = user_messages[0][1]
                last_user_prompt = user_messages[-1][1]
            if assistant_messages:
                last_assistant_response = assistant_messages[-1][1]

        except (IOError, Exception):
            return None

        # Get modified time from file
        modified_time = datetime.fromtimestamp(path.stat().st_mtime)

        # Compute content hash and get cached summary
        content_hash = compute_content_hash(first_user_prompt, last_assistant_response)
        summary = None
        if first_user_prompt:
            cache = SummaryCache()
            summary = cache.get(path.stem, content_hash)

        # Build project path and name
        project_path = Path(cwd) if cwd else Path(decode_path(project_dir))
        project_name = project_path.name

        return Session(
            id=path.stem,
            harness=self.name,
            raw_path=path,
            project_path=project_path,
            project_name=project_name,
            title=title,
            first_prompt=first_user_prompt,
            last_prompt=last_user_prompt,
            last_response=last_assistant_response,
            created_time=created_time,
            modified_time=modified_time,
            is_child=is_subagent,
            child_type=subagent_type,
            model=model,
            summary=summary,
            content_hash=content_hash,
            extra={"settings_path": str(settings_path)},
        )

    def get_resume_command(self, session: Session) -> str:
        return f"droid --resume {session.id}"

    def get_task_invocations(self, session: Session) -> list[dict]:
        """Parse Task tool invocations from this session."""
        if session.is_child:
            return []

        invocations = []
        try:
            with open(session.raw_path) as f:
                for line in f:
                    if '"name":"Task"' not in line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") != "message":
                            continue
                        msg = data.get("message", {})
                        if msg.get("role") != "assistant":
                            continue

                        content = msg.get("content", [])
                        if not isinstance(content, list):
                            continue

                        timestamp = data.get("timestamp")
                        ts = None
                        if timestamp:
                            try:
                                ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                            except (ValueError, TypeError):
                                pass

                        for item in content:
                            if isinstance(item, dict) and item.get("name") == "Task":
                                inp = item.get("input", {})
                                subagent_type = inp.get("subagent_type", "")
                                if subagent_type:
                                    invocations.append({
                                        "subagent_type": subagent_type,
                                        "timestamp": ts,
                                        "description": inp.get("description", "")
                                    })
                    except json.JSONDecodeError:
                        continue
        except (IOError, Exception):
            pass

        return invocations

    def find_children(self, parent: Session, all_sessions: list[Session]) -> list[Session]:
        """Find sub-agent sessions related to a parent session."""
        if parent.is_child:
            return []

        task_invocations = self.get_task_invocations(parent)
        if not task_invocations:
            return []

        # Filter to only Droid sessions that are children
        subagents = [s for s in all_sessions if s.harness == self.name and s.is_child]

        related = []
        for subagent in subagents:
            if not subagent.child_type:
                continue

            for task in task_invocations:
                if task["subagent_type"] != subagent.child_type:
                    continue

                # Check timestamp proximity (within 60 seconds)
                if task["timestamp"] and subagent.created_time:
                    time_diff = abs((subagent.created_time - task["timestamp"]).total_seconds())
                    if time_diff < 60:
                        related.append(subagent)
                        break
                # Fallback: check if subagent was created during parent session timeframe
                elif subagent.modified_time and parent.modified_time:
                    if subagent.modified_time >= parent.modified_time - timedelta(hours=2):
                        # Also check same cwd
                        if subagent.project_path == parent.project_path:
                            related.append(subagent)
                            break

        # Sort by created time
        related.sort(key=lambda s: s.created_time or s.modified_time)
        return related
