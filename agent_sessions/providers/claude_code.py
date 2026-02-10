"""Claude Code session provider."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..cache import MetadataCache, SummaryCache, compute_content_hash
from ..models import Session
from . import register_provider
from .base import SessionProvider, detect_automated_session, find_first_real_prompt, find_last_real_response


SESSIONS_DIR = Path.home() / ".claude" / "projects"
SUBAGENT_TITLE_PREFIX = "# Task Tool Invocation"


def decode_path(encoded: str) -> str:
    """Decode directory name back to original path."""
    return encoded.replace("-", "/")


def extract_text_content(content, text_only: bool = False) -> str:
    """Extract text from message content (handles both string and list formats)."""
    if isinstance(content, str):
        # String content - return as-is (skip system reminders)
        if content.strip().startswith("<system-reminder>"):
            return ""
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
                    content_str = str(item.get('content', ''))[:50]
                    texts.append(f"(tool_result: {content_str}...)")
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts)
    return str(content)


def detect_worker_session(first_prompt: str, project_dir: str) -> tuple[bool, str]:
    """Detect if a session is a worker/sub-agent based on prompt content and path.
    
    Returns (is_child, child_type) tuple.
    """
    path_lower = project_dir.lower()
    prompt_start = first_prompt[:800] if first_prompt else ""
    prompt_lower = prompt_start.lower()
    
    # 0. Check for automated/system sessions first
    is_auto, auto_type = detect_automated_session(first_prompt)
    if is_auto:
        return True, auto_type

    # 1. Warmup sessions
    if first_prompt and first_prompt.strip().lower() == "warmup":
        return True, "warmup"
    
    # 2. TORUS loop sessions
    if 'torus loop' in prompt_lower:
        return True, "torus-loop"
    
    # 3. Autopilot sessions
    if 'autopilot' in prompt_lower and ('no human review' in prompt_lower or 'no questions' in prompt_lower):
        return True, "autopilot"
    
    # 4. SPIRIT/WHEEL file reference pattern (TORUS orchestration)
    if prompt_start.strip().startswith('@') and ('@spirit.md' in prompt_lower or '@wheel.md' in prompt_lower):
        return True, "torus-orchestrated"
    
    # 5. Merkabah workers - path pattern
    if 'merkabah-workers' in path_lower:
        worker_match = re.search(r'worker-(\d+)', path_lower)
        if worker_match:
            return True, f"merkabah-worker-{worker_match.group(1)}"
        return True, "merkabah-worker"
    
    # 6. TORUSv3 workers
    if 'torusv3-workers' in path_lower or '-torusv3-workers-' in path_lower:
        worker_match = re.search(r'worker-(\d+)', path_lower)
        if worker_match:
            return True, f"torusv3-worker-{worker_match.group(1)}"
        return True, "torusv3-worker"
    
    if '# worker prompt' in prompt_lower and ('torusv3' in prompt_lower or 'one task' in prompt_lower):
        return True, "torusv3-worker"
    
    # 7. Ophanim workers
    if 'ophanim' in path_lower:
        return True, "ophanim-worker"
    if '@vision.md' in prompt_lower and '@altar.json' in prompt_lower:
        return True, "ophanim-worker"
    if '# wings.md' in prompt_lower and 'one task' in prompt_lower:
        return True, "ophanim-worker"
    
    # 8. Generic worker prompt pattern
    if prompt_start.strip().startswith("# Worker Prompt"):
        return True, "worker"
    
    # 9. Task tool invocation pattern
    if "subagent_type" in prompt_lower:
        match = re.search(r'subagent_type["\s:]+([a-zA-Z0-9_-]+)', first_prompt[:500])
        if match:
            return True, match.group(1)
        return True, "task-subagent"
    
    return False, ""


@register_provider
class ClaudeCodeProvider(SessionProvider):
    """Provider for Claude Code sessions."""

    name = "claude-code"
    display_name = "Claude Code"
    icon = "🧠"
    color = "cyan"

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

    def _session_from_cache(self, path: Path, cached: dict) -> Session:
        """Construct Session from cached metadata."""
        created_time = None
        if cached.get("created_time"):
            try:
                created_time = datetime.fromisoformat(cached["created_time"])
            except (ValueError, TypeError):
                pass

        modified_time = datetime.fromtimestamp(path.stat().st_mtime)

        # Get summary from summary cache
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
            extra={
                "version": cached.get("version", ""),
                "git_branch": cached.get("git_branch", ""),
            },
        )

    def parse_session(self, path: Path) -> Session | None:
        """Parse a Claude Code JSONL session file."""
        project_dir = path.parent.name

        # Check metadata cache first
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None

        cache = MetadataCache()
        cached = cache.get(path, mtime)
        if cached:
            return self._session_from_cache(path, cached)

        # Defaults
        model = "unknown"
        cwd = ""
        title = ""
        session_id = path.stem
        first_user_prompt = ""
        last_user_prompt = ""
        last_assistant_response = ""
        created_time: Optional[datetime] = None
        version = ""
        git_branch = ""
        is_subagent = False
        subagent_type = ""
        is_sidechain = False

        # Parse JSONL
        try:
            messages = []
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        msg_type = data.get("type")

                        # Skip non-message types
                        if msg_type in ("file-history-snapshot", "progress"):
                            continue

                        # Extract session metadata from first user message
                        if msg_type == "user":
                            if not cwd:
                                cwd = data.get("cwd", decode_path(project_dir))
                            if not version:
                                version = data.get("version", "")
                            if not git_branch:
                                git_branch = data.get("gitBranch", "")
                            if not session_id:
                                session_id = data.get("sessionId", path.stem)
                            # isSidechain is the authoritative sub-agent flag from Claude Code
                            if data.get("isSidechain"):
                                is_sidechain = True

                        # Get model from assistant messages
                        if msg_type == "assistant":
                            msg = data.get("message", {})
                            if msg.get("model") and model == "unknown":
                                model = msg.get("model")

                        # Extract message content
                        if msg_type in ("user", "assistant"):
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
                first_user_prompt = find_first_real_prompt(user_messages)
                last_user_prompt = user_messages[-1][1]
            if assistant_messages:
                last_assistant_response = find_last_real_response(assistant_messages)

        except (IOError, Exception):
            return None

        # Skip empty sessions (no messages at all)
        if not messages:
            return None

        # isSidechain from Claude Code is authoritative for sub-agent detection
        if is_sidechain:
            is_subagent = True
            subagent_type = subagent_type or "sidechain"
        
        # Also check prompt-based heuristics for worker/automated sessions
        if not is_subagent:
            is_subagent, subagent_type = detect_worker_session(first_user_prompt, project_dir)

        # Generate title from first prompt if not available
        if not title and first_user_prompt:
            # Take first line or first 80 chars
            first_line = first_user_prompt.split('\n')[0].strip()
            title = first_line[:80] if first_line else "Claude Code Session"

        # Get modified time from file
        modified_time = datetime.fromtimestamp(path.stat().st_mtime)

        # Compute content hash and get cached summary
        content_hash = compute_content_hash(first_user_prompt, last_assistant_response)
        summary = None
        if first_user_prompt:
            summary_cache = SummaryCache()
            summary = summary_cache.get(path.stem, content_hash)

        # Build project path and name
        project_path = Path(cwd) if cwd else Path(decode_path(project_dir))
        project_name = project_path.name

        # Cache the metadata for next time
        metadata = {
            "session_id": session_id,
            "project_path": str(project_path),
            "project_name": project_name,
            "title": title,
            "first_prompt": first_user_prompt[:2000],  # Truncate for cache size
            "last_prompt": last_user_prompt[:2000],
            "last_response": last_assistant_response[:2000],
            "created_time": created_time.isoformat() if created_time else None,
            "is_child": is_subagent,
            "child_type": subagent_type,
            "model": model,
            "content_hash": content_hash,
            "version": version,
            "git_branch": git_branch,
        }
        cache.set(path, mtime, metadata)

        return Session(
            id=session_id,
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
            extra={
                "version": version,
                "git_branch": git_branch,
            },
        )

    def get_resume_command(self, session: Session) -> str:
        return f"claude --resume {session.id}"

    def get_task_invocations(self, session: Session) -> list[dict]:
        """Parse Task tool invocations from this session."""
        if session.is_child:
            return []

        invocations = []
        try:
            with open(session.raw_path) as f:
                for line in f:
                    if '"name":"Task"' not in line and '"name": "Task"' not in line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") != "assistant":
                            continue
                        msg = data.get("message", {})

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

        # Filter to only Claude Code sessions that are children
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
                        if subagent.project_path == parent.project_path:
                            related.append(subagent)
                            break

        related.sort(key=lambda s: s.created_time or s.modified_time or datetime.min)
        return related

    def get_session_messages(self, session: Session) -> list[dict]:
        messages = []
        try:
            with open(session.raw_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        msg_type = data.get("type")
                        
                        if msg_type not in ("user", "assistant"):
                            continue
                            
                        msg = data.get("message", {})
                        role = msg.get("role")
                        if role not in ("user", "assistant"):
                            continue

                        text_only = (role == "user")
                        content = extract_text_content(msg.get("content", ""), text_only=text_only)
                        if not content or "<system-reminder>" in content[:100]:
                            continue

                        timestamp = data.get("timestamp")
                        msg_id = data.get("uuid", f"{session.id}_{len(messages)}")

                        messages.append({
                            "id": msg_id,
                            "role": role,
                            "content": content,
                            "timestamp": timestamp,
                        })
                    except json.JSONDecodeError:
                        continue
        except (IOError, Exception):
            pass
        return messages
