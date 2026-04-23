"""Codex session provider."""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..cache import MetadataCache, SummaryCache, compute_content_hash
from ..models import Session
from . import register_provider
from .base import SessionProvider, find_first_real_prompt, find_last_real_response


SESSIONS_DIR = Path.home() / ".codex" / "sessions"
SESSION_INDEX_PATH = Path.home() / ".codex" / "session_index.jsonl"


def _parse_datetime(value: str | None) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp into a datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_epoch_seconds(value: str | None) -> Optional[int]:
    """Parse an ISO 8601 timestamp into epoch seconds."""
    dt = _parse_datetime(value)
    if not dt:
        return None
    return int(dt.timestamp())


def _extract_event_text(payload: dict[str, Any]) -> str:
    """Extract conversational text from an event payload."""
    for key in ("text", "content", "message", "delta"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _extract_response_text(
    content: Any,
    *,
    allowed_types: tuple[str, ...],
) -> str:
    """Extract text blocks from response_item message content."""
    if not isinstance(content, list):
        return ""

    texts = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in allowed_types and isinstance(item.get("text"), str):
            text = item["text"].strip()
            if text:
                texts.append(text)
    return " ".join(texts)


@lru_cache(maxsize=1)
def _load_session_titles() -> dict[str, str]:
    """Load optional thread titles from Codex's session index."""
    if not SESSION_INDEX_PATH.exists():
        return {}

    titles = {}
    try:
        with open(SESSION_INDEX_PATH) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                session_id = row.get("id")
                thread_name = row.get("thread_name")
                if isinstance(session_id, str) and isinstance(thread_name, str) and thread_name.strip():
                    titles[session_id] = thread_name.strip()
    except OSError:
        return {}

    return titles


def _extract_subagent_context(meta: dict[str, Any]) -> tuple[bool, Optional[str], str, str]:
    """Extract explicit child-session metadata from session_meta payload."""
    source = meta.get("source")
    parent_id = None
    agent_nickname = ""
    agent_role = ""
    has_subagent_source = False

    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            thread_spawn = subagent.get("thread_spawn")
            if isinstance(thread_spawn, dict):
                has_subagent_source = True
                parent_id = thread_spawn.get("parent_thread_id")
                agent_nickname = thread_spawn.get("agent_nickname", "") or ""
                agent_role = thread_spawn.get("agent_role", "") or ""

    if not agent_nickname:
        agent_nickname = meta.get("agent_nickname", "") or ""
    if not agent_role:
        agent_role = meta.get("agent_role", "") or ""

    is_child = has_subagent_source or bool(agent_role or agent_nickname)
    if is_child and not parent_id:
        parent_id = meta.get("forked_from_id")

    child_type = agent_role or agent_nickname or ("subagent" if is_child else "")
    return is_child, parent_id, child_type, agent_nickname


def _parse_codex_file(path: Path) -> dict[str, Any]:
    """Parse a Codex session JSONL file into metadata and messages."""
    session_meta: dict[str, Any] = {}
    session_meta_locked = False
    messages: list[dict[str, Any]] = []
    fallback_messages: list[dict[str, Any]] = []
    tool_calls: set[str] = set()
    created_time = None
    modified_time = None
    model = "unknown"

    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                row_timestamp = _parse_datetime(row.get("timestamp"))
                if row_timestamp and (modified_time is None or row_timestamp > modified_time):
                    modified_time = row_timestamp

                row_type = row.get("type")

                if row_type == "session_meta":
                    payload = row.get("payload", {})
                    if isinstance(payload, dict):
                        payload_id = payload.get("id")
                        if payload_id == path.stem:
                            session_meta = payload
                            session_meta_locked = True
                            session_timestamp = _parse_datetime(payload.get("timestamp"))
                            if session_timestamp:
                                created_time = session_timestamp
                        elif not session_meta and not session_meta_locked:
                            session_meta = payload
                            session_timestamp = _parse_datetime(payload.get("timestamp"))
                            if session_timestamp:
                                created_time = session_timestamp

                elif row_type == "turn_context":
                    payload = row.get("payload", {})
                    if isinstance(payload, dict) and payload.get("model"):
                        model = payload.get("model", model)

                elif row_type == "event_msg":
                    payload = row.get("payload", {})
                    if not isinstance(payload, dict):
                        continue

                    event_type = payload.get("type")
                    if event_type not in ("user_message", "agent_message"):
                        continue

                    text = _extract_event_text(payload)
                    if not text:
                        continue

                    role = "user" if event_type == "user_message" else "assistant"
                    messages.append(
                        {
                            "id": f"{path.stem}_msg_{len(messages)}",
                            "role": role,
                            "content": text,
                            "timestamp": _parse_epoch_seconds(row.get("timestamp")),
                        }
                    )

                elif row_type == "response_item":
                    payload = row.get("payload", {})
                    if not isinstance(payload, dict):
                        continue

                    payload_type = payload.get("type")
                    if payload_type == "function_call":
                        name = payload.get("name")
                        if isinstance(name, str) and name:
                            tool_calls.add(name)
                    elif payload_type == "message":
                        role = payload.get("role")
                        if role not in ("user", "assistant"):
                            continue

                        allowed_types = ("input_text",) if role == "user" else ("output_text",)
                        text = _extract_response_text(
                            payload.get("content"),
                            allowed_types=allowed_types,
                        )
                        if not text:
                            continue

                        fallback_messages.append(
                            {
                                "id": f"{path.stem}_fallback_{len(fallback_messages)}",
                                "role": role,
                                "content": text,
                                "timestamp": _parse_epoch_seconds(row.get("timestamp")),
                            }
                        )
    except OSError:
        return {
            "session_meta": {},
            "messages": [],
            "tool_calls": [],
            "created_time": None,
            "modified_time": None,
            "model": "unknown",
        }

    if not messages:
        messages = fallback_messages

    if created_time is None and messages:
        first_ts = messages[0].get("timestamp")
        if isinstance(first_ts, int):
            created_time = datetime.fromtimestamp(first_ts)

    if modified_time is None:
        modified_time = created_time

    return {
        "session_meta": session_meta,
        "messages": messages,
        "tool_calls": sorted(tool_calls),
        "created_time": created_time,
        "modified_time": modified_time,
        "model": model,
    }


@register_provider
class CodexProvider(SessionProvider):
    """Provider for Codex CLI sessions."""

    name = "codex"
    display_name = "Codex"
    icon = "✦"
    color = "yellow"

    def get_sessions_dir(self) -> Path:
        return SESSIONS_DIR

    def discover_session_files(self) -> list[Path]:
        """Discover all Codex JSONL session files."""
        sessions_dir = self.get_sessions_dir()
        if not sessions_dir.exists():
            return []
        return sorted(sessions_dir.rglob("*.jsonl"))

    def _session_from_cache(self, path: Path, cached: dict[str, Any]) -> Session:
        """Construct a Session from cached metadata."""
        created_time = _parse_datetime(cached.get("created_time"))
        modified_time = _parse_datetime(cached.get("modified_time"))
        if not modified_time:
            try:
                modified_time = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                modified_time = created_time

        content_hash = cached.get("content_hash", "")
        summary = None
        if cached.get("first_prompt"):
            summary = SummaryCache().get(cached.get("session_id", path.stem), content_hash)

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
            parent_id=cached.get("parent_id"),
            model=cached.get("model", "unknown"),
            tool_calls=cached.get("tool_calls", []),
            summary=summary,
            content_hash=content_hash,
            extra=cached.get("extra", {}),
        )

    def parse_session(self, path: Path) -> Session | None:
        """Parse a Codex session JSONL file."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None

        cache = MetadataCache()
        cached = cache.get(path, mtime)
        if cached:
            return self._session_from_cache(path, cached)

        parsed = _parse_codex_file(path)
        messages = parsed["messages"]
        if not messages and not parsed["session_meta"]:
            return None

        session_meta = parsed["session_meta"]
        session_id = session_meta.get("id", path.stem)
        cwd = session_meta.get("cwd", "")
        project_path = Path(cwd) if cwd else Path.home()
        project_name = project_path.name or "Codex"

        user_messages = [(m["role"], m["content"]) for m in messages if m.get("role") == "user"]
        assistant_messages = [(m["role"], m["content"]) for m in messages if m.get("role") == "assistant"]

        first_prompt = find_first_real_prompt(user_messages) if user_messages else ""
        last_prompt = user_messages[-1][1] if user_messages else ""
        last_response = find_last_real_response(assistant_messages) if assistant_messages else ""

        thread_title = _load_session_titles().get(session_id, "")
        title = thread_title
        if not title and first_prompt:
            first_line = first_prompt.split("\n")[0].strip()
            title = first_line[:80] if first_line else "Codex Session"
        if not title:
            title = "Codex Session"

        is_child, parent_id, child_type, agent_nickname = _extract_subagent_context(session_meta)

        content_hash = compute_content_hash(first_prompt, last_response)
        summary = None
        if first_prompt:
            summary = SummaryCache().get(session_id, content_hash)

        created_time = parsed["created_time"]
        modified_time = parsed["modified_time"]
        if modified_time is None:
            modified_time = datetime.fromtimestamp(mtime)

        extra = {
            "originator": session_meta.get("originator", ""),
            "cli_version": session_meta.get("cli_version", ""),
            "model_provider": session_meta.get("model_provider", ""),
            "agent_nickname": agent_nickname,
            "thread_name": thread_title,
        }

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
            "parent_id": parent_id,
            "model": parsed["model"],
            "tool_calls": parsed["tool_calls"],
            "content_hash": content_hash,
            "extra": extra,
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
            parent_id=parent_id,
            model=parsed["model"],
            tool_calls=parsed["tool_calls"],
            summary=summary,
            content_hash=content_hash,
            extra=extra,
        )

    def get_resume_command(self, session: Session) -> str:
        return f"codex resume {session.id}"

    def find_children(self, parent: Session, all_sessions: list[Session]) -> list[Session]:
        """Find explicitly linked Codex child sessions."""
        if parent.is_child:
            return []

        children = [
            session
            for session in all_sessions
            if session.harness == self.name and session.parent_id == parent.id
        ]
        children.sort(key=lambda s: s.created_time or s.modified_time or datetime.min)
        return children

    def get_session_messages(self, session: Session) -> list[dict]:
        return _parse_codex_file(session.raw_path)["messages"]
