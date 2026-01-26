#!/usr/bin/env python3
"""Droid Sessions Browser TUI - Browse and resume Factory AI sessions.

Features split-pane view separating parent orchestrator sessions from sub-agent sessions.
"""

import hashlib
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


SESSIONS_DIR = Path.home() / ".factory" / "sessions"
SUMMARY_CACHE_PATH = Path.home() / ".factory" / "session-summaries.json"
SUBAGENT_TITLE_PREFIX = "# Task Tool Invocation"
HAIKU_MODEL = "claude-haiku-4-5-20251001"


class SummaryCache:
    """Thread-safe cache for AI-generated session summaries."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
            cls._instance._dirty = False
            cls._instance._load()
        return cls._instance
    
    def _load(self):
        """Load cache from disk."""
        if SUMMARY_CACHE_PATH.exists():
            try:
                with open(SUMMARY_CACHE_PATH) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}
    
    def save(self):
        """Save cache to disk if dirty."""
        with self._lock:
            if not self._dirty:
                return
            try:
                SUMMARY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(SUMMARY_CACHE_PATH, "w") as f:
                    json.dump(self._data, f, indent=2)
                self._dirty = False
            except IOError:
                pass
    
    def get(self, session_id: str, content_hash: str) -> Optional[str]:
        """Get cached summary if hash matches."""
        with self._lock:
            entry = self._data.get(session_id)
            if entry and entry.get("hash") == content_hash:
                return entry.get("summary")
            return None
    
    def set(self, session_id: str, content_hash: str, summary: str):
        """Cache a summary."""
        with self._lock:
            self._data[session_id] = {"hash": content_hash, "summary": summary}
            self._dirty = True


def compute_session_hash(first_prompt: str, last_response: str) -> str:
    """Compute hash of session content for cache invalidation."""
    content = f"{first_prompt[:500]}|{last_response[:500]}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def generate_summary_sync(first_prompt: str, last_response: str) -> Optional[str]:
    """Generate a summary using Claude Haiku (synchronous)."""
    if not HAS_ANTHROPIC:
        return None
    
    try:
        client = anthropic.Anthropic()
        
        context = f"""SESSION START (user request):
{first_prompt[:1500]}

SESSION END (final assistant response):
{last_response[:1500]}"""
        
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=60,
            messages=[{
                "role": "user",
                "content": f"""Summarize this coding session in 6-10 words. Focus on WHAT WAS DONE, not what was asked. Use past tense verbs. No quotes or punctuation at end.

{context}

Summary:"""
            }]
        )
        
        summary = response.content[0].text.strip()
        # Clean up common artifacts
        summary = summary.strip('"\'').rstrip('.')
        return summary[:80] if summary else None
        
    except Exception:
        return None


def decode_path(encoded: str) -> str:
    """Decode directory name back to original path."""
    return encoded.replace("-", "/")


def truncate(text: str, max_len: int = 100) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def extract_text_content(content, text_only: bool = False) -> str:
    """Extract text from message content (handles both string and list formats).
    
    Args:
        content: The message content (string or list)
        text_only: If True, only extract actual text, skip tool_results
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text", "")
                    # Skip system reminders embedded in text
                    if text and not text.strip().startswith("<system-reminder>"):
                        texts.append(text)
                elif item.get("type") == "tool_result" and not text_only:
                    texts.append(f"(tool_result: {truncate(str(item.get('content', '')), 50)})")
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts)
    return str(content)


class SessionInfo:
    """Container for session metadata."""
    
    def __init__(self, jsonl_path: Path):
        self.jsonl_path = jsonl_path
        self.session_id = jsonl_path.stem
        self.settings_path = jsonl_path.with_suffix(".settings.json")
        self.project_dir = jsonl_path.parent.name
        self.cwd = ""
        self.title = "Untitled Session"
        self.model = "unknown"
        self.first_user_prompt = ""
        self.last_user_prompt = ""
        self.last_assistant_response = ""
        self.modified_time = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
        self.created_time: Optional[datetime] = None
        
        # Sub-agent detection
        self.is_subagent = False
        self.subagent_type = ""
        
        # Task tool invocations (for parent sessions)
        self._task_invocations: Optional[list] = None
        
        self._load_metadata()
    
    def _load_metadata(self):
        """Load session metadata from files."""
        # Load settings
        if self.settings_path.exists():
            try:
                with open(self.settings_path) as f:
                    settings = json.load(f)
                    self.model = settings.get("model", "unknown")
            except (json.JSONDecodeError, IOError):
                pass
        
        # Load session data from JSONL
        try:
            messages = []
            with open(self.jsonl_path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "session_start":
                            self.title = data.get("title", data.get("sessionTitle", "Untitled"))[:80]
                            self.cwd = data.get("cwd", decode_path(self.project_dir))
                            
                            # Detect sub-agent sessions
                            if self.title.startswith(SUBAGENT_TITLE_PREFIX):
                                self.is_subagent = True
                                match = re.search(r'Subagent type: ([a-zA-Z0-9_-]+)', self.title)
                                if match:
                                    self.subagent_type = match.group(1)
                                    
                        elif data.get("type") == "message":
                            msg = data.get("message", {})
                            role = msg.get("role")
                            
                            # Capture timestamp for first message
                            if self.created_time is None:
                                ts = data.get("timestamp")
                                if ts:
                                    try:
                                        self.created_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                    except (ValueError, TypeError):
                                        pass
                            
                            # For user messages, only extract actual text (not tool_results)
                            # For assistant messages, include everything
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
                self.first_user_prompt = user_messages[0][1]
                self.last_user_prompt = user_messages[-1][1]
            if assistant_messages:
                self.last_assistant_response = assistant_messages[-1][1]
                
        except (IOError, Exception):
            pass
    
    def get_task_invocations(self) -> list[dict]:
        """Parse and cache Task tool invocations from this session."""
        if self._task_invocations is not None:
            return self._task_invocations
        
        self._task_invocations = []
        if self.is_subagent:
            return self._task_invocations
        
        try:
            with open(self.jsonl_path) as f:
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
                                    self._task_invocations.append({
                                        "subagent_type": subagent_type,
                                        "timestamp": ts,
                                        "description": inp.get("description", "")
                                    })
                    except json.JSONDecodeError:
                        continue
        except (IOError, Exception):
            pass
        
        return self._task_invocations
    
    @property
    def content_hash(self) -> str:
        """Get hash of session content for cache invalidation."""
        return compute_session_hash(self.first_user_prompt, self.last_assistant_response)
    
    @property
    def summary(self) -> Optional[str]:
        """Get cached AI summary if available."""
        if not self.first_user_prompt:
            return None
        cache = SummaryCache()
        return cache.get(self.session_id, self.content_hash)
    
    @summary.setter
    def summary(self, value: str):
        """Cache a summary for this session."""
        cache = SummaryCache()
        cache.set(self.session_id, self.content_hash, value)
    
    def needs_summary(self) -> bool:
        """Check if this session needs a summary generated."""
        if self.is_subagent:
            return False
        if not self.first_user_prompt or not self.last_assistant_response:
            return False
        return self.summary is None
    
    @property
    def display_title(self) -> str:
        """Get display-friendly title."""
        if self.is_subagent:
            # For sub-agents, show the type and description
            return f"{self.subagent_type}"
        if self.title and self.title != "New Session" and not self.title.startswith(SUBAGENT_TITLE_PREFIX):
            return truncate(self.title, 50)
        return truncate(Path(self.cwd).name if self.cwd else "Unknown", 50)
    
    @property
    def project_name(self) -> str:
        """Get project name from cwd."""
        if self.cwd:
            return Path(self.cwd).name
        return decode_path(self.project_dir).split("/")[-1]


def load_all_sessions() -> tuple[list[SessionInfo], list[SessionInfo]]:
    """Load all sessions, separated into parent and sub-agent lists."""
    parent_sessions = []
    subagent_sessions = []
    
    if not SESSIONS_DIR.exists():
        return parent_sessions, subagent_sessions
    
    for project_dir in SESSIONS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            try:
                session = SessionInfo(jsonl_file)
                if session.is_subagent:
                    subagent_sessions.append(session)
                else:
                    parent_sessions.append(session)
            except Exception:
                continue
    
    # Sort by modified time, newest first
    parent_sessions.sort(key=lambda s: s.modified_time, reverse=True)
    subagent_sessions.sort(key=lambda s: s.modified_time, reverse=True)
    
    return parent_sessions, subagent_sessions


class SearchResult:
    """A single search result with context."""
    
    def __init__(self, session: SessionInfo, role: str, match_text: str, 
                 context_before: list[str], context_after: list[str], line_num: int):
        self.session = session
        self.role = role  # "user" or "assistant"
        self.match_text = match_text
        self.context_before = context_before  # Lines before match
        self.context_after = context_after    # Lines after match
        self.line_num = line_num


def search_session(session: SessionInfo, query: str, max_results: int = 50) -> list[SearchResult]:
    """Search a session's JSONL for query matches, returning results with context."""
    results = []
    query_lower = query.lower()
    
    try:
        with open(session.jsonl_path) as f:
            all_messages = []
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("type") == "message":
                        msg = data.get("message", {})
                        role = msg.get("role", "")
                        if role in ("user", "assistant"):
                            content = extract_text_content(msg.get("content", ""), text_only=(role == "user"))
                            if content and "<system-reminder>" not in content[:100]:
                                all_messages.append((role, content))
                except json.JSONDecodeError:
                    continue
            
            # Search through messages
            for idx, (role, content) in enumerate(all_messages):
                if query_lower in content.lower():
                    # Find all occurrences in this message
                    lines = content.split("\n")
                    for line_num, line in enumerate(lines):
                        if query_lower in line.lower():
                            # Get context
                            context_before = lines[max(0, line_num-2):line_num]
                            context_after = lines[line_num+1:line_num+3]
                            
                            results.append(SearchResult(
                                session=session,
                                role=role,
                                match_text=line,
                                context_before=context_before,
                                context_after=context_after,
                                line_num=line_num
                            ))
                            
                            if len(results) >= max_results:
                                return results
    except (IOError, Exception):
        pass
    
    return results


def search_all_sessions(sessions: list[SessionInfo], query: str) -> dict[str, list[SearchResult]]:
    """Search all sessions and return results grouped by session ID."""
    results_by_session = {}
    
    for session in sessions:
        results = search_session(session, query)
        if results:
            results_by_session[session.session_id] = results
    
    return results_by_session


def find_related_subagents(parent: SessionInfo, all_subagents: list[SessionInfo]) -> list[SessionInfo]:
    """Find sub-agent sessions related to a parent session."""
    if parent.is_subagent:
        return []
    
    task_invocations = parent.get_task_invocations()
    if not task_invocations:
        return []
    
    related = []
    for subagent in all_subagents:
        if not subagent.subagent_type:
            continue
        
        # Match by subagent_type and timestamp proximity
        for task in task_invocations:
            if task["subagent_type"] != subagent.subagent_type:
                continue
            
            # Check timestamp proximity (within 60 seconds)
            if task["timestamp"] and subagent.created_time:
                time_diff = abs((subagent.created_time - task["timestamp"]).total_seconds())
                if time_diff < 60:
                    related.append(subagent)
                    break
            # Fallback: check if subagent was created during parent session timeframe
            elif subagent.modified_time >= parent.modified_time - timedelta(hours=2):
                # Also check same cwd
                if subagent.cwd and parent.cwd and subagent.cwd == parent.cwd:
                    related.append(subagent)
                    break
    
    # Sort by created time
    related.sort(key=lambda s: s.created_time or s.modified_time)
    return related


class ParentSessionItem(ListItem):
    """List item for parent sessions."""
    
    def __init__(self, session: SessionInfo, subagent_count: int = 0):
        super().__init__()
        self.session = session
        self.subagent_count = subagent_count
        self._static: Optional[Static] = None
    
    def compose(self) -> ComposeResult:
        self._static = Static(self._build_text(100))  # Default width
        yield self._static
    
    def on_resize(self, event) -> None:
        """Update text when resized."""
        if self._static:
            self._static.update(self._build_text(self.size.width))
    
    def _build_text(self, width: int) -> Text:
        """Build the display text based on available width."""
        date_str = self.session.modified_time.strftime("%m-%d %H:%M")
        project = self.session.project_name
        
        # Prefer AI summary over raw prompt
        summary = self.session.summary
        if summary:
            description = summary
            desc_style = "bold white"
        else:
            # Fallback to truncated prompt
            description = self.session.first_user_prompt or self.session.display_title
            desc_style = "dim white"
        description = description.replace("\n", " ").strip()
        
        text = Text()
        text.append(f"{date_str}", style="cyan")
        text.append(" │ ", style="dim")
        text.append(f"{project[:12]:<12}", style="green")
        text.append(" │ ", style="dim")
        
        # Calculate remaining width for description
        # Fixed parts: date(11) + sep(3) + project(12) + sep(3) + padding(4) = ~33
        prefix_width = 33
        if self.subagent_count > 0:
            count_str = f"({self.subagent_count}) "
            text.append(count_str, style="yellow bold")
            prefix_width += len(count_str)
        
        desc_width = max(20, width - prefix_width)
        text.append(truncate(description, desc_width), style=desc_style)
        
        return text
    
    def refresh_text(self):
        """Refresh the display text (call after summary is generated)."""
        if self._static:
            self._static.update(self._build_text(self.size.width))


class SubagentSessionItem(ListItem):
    """List item for sub-agent sessions."""
    
    def __init__(self, session: SessionInfo, is_highlighted: bool = False):
        super().__init__()
        self.session = session
        self.is_highlighted = is_highlighted
        self._static: Optional[Static] = None
    
    def compose(self) -> ComposeResult:
        self._static = Static(self._build_text(100))  # Default width
        yield self._static
    
    def on_resize(self, event) -> None:
        """Update text when resized."""
        if self._static:
            self._static.update(self._build_text(self.size.width))
    
    def _build_text(self, width: int) -> Text:
        """Build the display text based on available width."""
        text = Text()
        
        if self.is_highlighted:
            text.append("★ ", style="yellow bold")
        else:
            text.append("  ", style="dim")
        
        text.append(f"{self.session.subagent_type:<18}", style="cyan bold")
        text.append(" │ ", style="dim")
        
        # Calculate remaining width: star(2) + type(18) + sep(3) + padding(4) = ~27
        prefix_width = 27
        desc_width = max(20, width - prefix_width)
        
        desc = self.session.first_user_prompt or "(no prompt)"
        desc = desc.replace("\n", " ").strip()
        text.append(truncate(desc, desc_width), style="white")
        
        return text


class SearchResultItem(ListItem):
    """List item for search results."""
    
    def __init__(self, result: SearchResult, query: str):
        super().__init__()
        self.result = result
        self.query = query
        self._static: Optional[Static] = None
    
    def compose(self) -> ComposeResult:
        self._static = Static(self._build_text(100))
        yield self._static
    
    def on_resize(self, event) -> None:
        if self._static:
            self._static.update(self._build_text(self.size.width))
    
    def _build_text(self, width: int) -> Text:
        text = Text()
        
        # Role indicator
        if self.result.role == "user":
            text.append("U ", style="green bold")
        else:
            text.append("A ", style="magenta bold")
        
        text.append("│ ", style="dim")
        
        # Show matched line with highlighting
        match_line = self.result.match_text.replace("\n", " ").strip()
        prefix_width = 5
        max_len = max(20, width - prefix_width)
        
        # Try to highlight the query in the match
        match_lower = match_line.lower()
        query_lower = self.query.lower()
        idx = match_lower.find(query_lower)
        
        if idx >= 0 and len(match_line) <= max_len:
            text.append(match_line[:idx])
            text.append(match_line[idx:idx+len(self.query)], style="black on yellow")
            text.append(match_line[idx+len(self.query):])
        else:
            text.append(truncate(match_line, max_len))
        
        return text


class SessionDetailPanel(ScrollableContainer, can_focus=True):
    """Scrollable panel showing session details."""
    
    def __init__(self, id: str = None):
        super().__init__(id=id)
        self.session: Optional[SessionInfo] = None
        self._content = Static("", markup=False)
    
    def compose(self) -> ComposeResult:
        yield self._content
    
    def update(self, text: Text) -> None:
        """Update the content."""
        self._content.update(text)
    
    def show_session(self, session: SessionInfo, subagent_count: int = 0):
        """Update display with session info."""
        self.session = session
        
        text = Text()
        
        text.append("━━━ Session Details ━━━\n", style="bold cyan")
        text.append("\n")
        
        if session.is_subagent:
            text.append("Type: ", style="bold")
            text.append("SUB-AGENT\n", style="yellow bold")
            text.append("Agent: ", style="bold")
            text.append(f"{session.subagent_type}\n", style="cyan bold")
        else:
            text.append("Type: ", style="bold")
            text.append("PARENT SESSION\n", style="green bold")
            if subagent_count > 0:
                text.append("Sub-agents: ", style="bold")
                text.append(f"{subagent_count}\n", style="yellow")
        
        text.append("Title: ", style="bold")
        text.append(f"{session.display_title}\n")
        text.append("Path: ", style="bold")
        text.append(f"{session.cwd}\n", style="dim")
        text.append("Date: ", style="bold")
        text.append(f"{session.modified_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        text.append("Model: ", style="bold")
        text.append(f"{session.model}\n", style="yellow")
        text.append("Session ID: ", style="bold")
        text.append(f"{session.session_id}\n", style="dim")
        text.append("\n")
        
        # Original prompt - show full text (scrollable panel handles overflow)
        text.append("┌─ Original Prompt ─────────────────────\n", style="bold green")
        if session.first_user_prompt:
            # Show full prompt - the panel is scrollable
            prompt_text = session.first_user_prompt[:2000]  # Reasonable limit for display
            for pl in prompt_text.split("\n"):
                text.append("│ ", style="green")
                text.append(f"{pl}\n")
            if len(session.first_user_prompt) > 2000:
                text.append("│ ", style="green")
                text.append("... (truncated)\n", style="dim")
        else:
            text.append("│ ", style="green")
            text.append("(no prompt found)\n", style="dim")
        text.append("└───────────────────────────────────────\n", style="green")
        text.append("\n")
        
        # Last response
        max_resp = 1000 if session.is_subagent else 2000
        text.append("┌─ Last Response ────────────────────────\n", style="bold magenta")
        if session.last_assistant_response:
            resp_text = session.last_assistant_response[:max_resp]
            for rl in resp_text.split("\n"):
                text.append("│ ", style="magenta")
                text.append(f"{rl}\n")
            if len(session.last_assistant_response) > max_resp:
                text.append("│ ", style="magenta")
                text.append("... (truncated)\n", style="dim")
        else:
            text.append("│ ", style="magenta")
            text.append("(no response found)\n", style="dim")
        text.append("└───────────────────────────────────────\n", style="magenta")
        text.append("\n")
        
        # Resume command
        text.append("━━━ Resume Command ━━━\n", style="bold yellow")
        text.append("\n")
        text.append(f" droid --resume {session.session_id} ", style="bold white on blue")
        text.append("\n\n")
        text.append("Press ", style="dim")
        text.append("Enter", style="bold")
        text.append(" to copy | ", style="dim")
        text.append("r", style="bold")
        text.append(" to resume | ", style="dim")
        text.append("Tab", style="bold")
        text.append(" switch panes", style="dim")
        
        self.update(text)
    
    def show_search_result(self, result: SearchResult, query: str):
        """Show a search result with highlighted context."""
        self.session = result.session
        
        text = Text()
        
        text.append("━━━ Search Result ━━━\n", style="bold yellow")
        text.append("\n")
        
        text.append("Session: ", style="bold")
        text.append(f"{result.session.display_title}\n")
        text.append("Path: ", style="bold")
        text.append(f"{result.session.cwd}\n", style="dim")
        text.append("Role: ", style="bold")
        if result.role == "user":
            text.append("User\n", style="green bold")
        else:
            text.append("Assistant\n", style="magenta bold")
        text.append("\n")
        
        # Context before
        text.append("┌─ Context ──────────────────────────────\n", style="bold cyan")
        for line in result.context_before:
            text.append("│ ", style="cyan dim")
            text.append(f"{line}\n", style="dim")
        
        # Matched line with highlighting
        text.append("│ ", style="cyan")
        match_line = result.match_text
        match_lower = match_line.lower()
        query_lower = query.lower()
        idx = match_lower.find(query_lower)
        
        if idx >= 0:
            text.append(match_line[:idx])
            text.append(match_line[idx:idx+len(query)], style="black on yellow bold")
            text.append(match_line[idx+len(query):])
        else:
            text.append(match_line)
        text.append("\n")
        
        # Context after
        for line in result.context_after:
            text.append("│ ", style="cyan dim")
            text.append(f"{line}\n", style="dim")
        text.append("└───────────────────────────────────────\n", style="cyan")
        text.append("\n")
        
        # Resume command
        text.append("━━━ Resume Command ━━━\n", style="bold yellow")
        text.append("\n")
        text.append(f" droid --resume {result.session.session_id} ", style="bold white on blue")
        text.append("\n\n")
        text.append("Press ", style="dim")
        text.append("Enter", style="bold")
        text.append(" to copy | ", style="dim")
        text.append("Escape", style="bold")
        text.append(" to clear search", style="dim")
        
        self.update(text)
        self.scroll_home()
    
    def clear_display(self):
        """Clear the display."""
        self.session = None
        text = Text("Select a session to view details", style="dim")
        self.update(text)


class DroidSessionsBrowser(App):
    """TUI for browsing Droid sessions with split parent/sub-agent panes."""
    
    CSS = """
    Screen {
        layout: horizontal;
    }
    
    #left-container {
        width: 55%;
        height: 100%;
    }
    
    #parent-container {
        height: 60%;
        border: solid $primary;
    }
    
    #subagent-container {
        height: 40%;
        border: solid $warning;
    }
    
    #detail-container {
        width: 45%;
        height: 100%;
        border: solid $secondary;
        padding: 1;
    }
    
    #parent-list, #subagent-list {
        height: 1fr;
    }
    
    .list-header {
        height: auto;
        background: $surface;
        padding: 0 1;
        text-style: bold;
    }
    
    #parent-header {
        color: $primary;
    }
    
    #subagent-header {
        color: $warning;
    }
    
    #search-input {
        display: none;
        height: 3;
        border: solid $warning;
        padding: 0 1;
    }
    
    #search-input.visible {
        display: block;
    }
    
    #detail-panel {
        height: 100%;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }
    
    #detail-panel:focus {
        border: solid $success;
    }
    
    ParentSessionItem, SubagentSessionItem, SearchResultItem {
        height: 1;
        padding: 0 1;
    }
    
    ParentSessionItem:hover, SubagentSessionItem:hover, SearchResultItem:hover {
        background: $surface-lighten-1;
    }
    
    ListView:focus > ListItem.-active {
        background: $primary-darken-1;
    }
    
    ListView.-has-focus > ListItem.-active {
        background: $primary;
    }
    
    #subagent-container.dimmed {
        opacity: 0.5;
    }
    
    Footer {
        background: $surface;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "copy_command", "Copy"),
        Binding("r", "resume_session", "Resume"),
        Binding("tab", "switch_pane", "Tab: Lists", priority=True),
        Binding("shift+tab", "focus_detail", "Detail", priority=True),
        Binding("escape", "back_to_list", "Back"),
        Binding("slash", "activate_search", "/ Search"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("home", "cursor_home", "Home", show=False, priority=True),
        Binding("end", "cursor_end", "End", show=False, priority=True),
        Binding("pageup", "cursor_page_up", "PgUp", show=False, priority=True),
        Binding("pagedown", "cursor_page_down", "PgDn", show=False, priority=True),
    ]
    
    def __init__(self):
        super().__init__()
        self.parent_sessions: list[SessionInfo] = []
        self.subagent_sessions: list[SessionInfo] = []
        self.current_subagents: list[SessionInfo] = []
        self.selected_session: Optional[SessionInfo] = None
        self.focus_pane = "parent"  # "parent", "subagent", or "detail"
        self._last_left_pane = "parent"  # Remember which left pane was active before going to detail
        self._subagent_cache: dict[str, list[SessionInfo]] = {}
        # Search state
        self._search_mode = False
        self._search_query = ""
        self._search_results: dict[str, list[SearchResult]] = {}
        self._filtered_parents: list[SessionInfo] = []
        self._current_session_results: list[SearchResult] = []
        # Summary generation state
        self._summary_queue: Queue = Queue()
        self._summary_generating = False
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-container"):
                yield Input(placeholder="Search sessions... (Enter to search, Escape to cancel)", id="search-input")
                with Vertical(id="parent-container"):
                    yield Static("[bold]Sessions[/] [dim](newest first)[/]", id="parent-header", classes="list-header")
                    yield ListView(id="parent-list")
                with Vertical(id="subagent-container"):
                    yield Static("[bold]Sub-agents[/] [dim](for selected session)[/]", id="subagent-header", classes="list-header")
                    yield ListView(id="subagent-list")
            with Vertical(id="detail-container"):
                yield SessionDetailPanel(id="detail-panel")
        yield Footer()
    
    def on_mount(self):
        """Load sessions when app mounts."""
        self.title = "Droid Sessions Browser"
        self.parent_sessions, self.subagent_sessions = load_all_sessions()
        
        parent_list = self.query_one("#parent-list", ListView)
        
        # Pre-compute subagent counts for display
        for session in self.parent_sessions:
            subagents = self._get_related_subagents(session)
            parent_list.append(ParentSessionItem(session, len(subagents)))
        
        if not self.parent_sessions:
            detail = self.query_one("#detail-panel", SessionDetailPanel)
            text = Text()
            text.append("No sessions found!", style="bold red")
            text.append(f"\n\nSessions directory: {SESSIONS_DIR}")
            detail.update(text)
        else:
            parent_list.index = 0
            parent_list.focus()
            # Start background summary generation
            self._start_summary_generation()
    
    def _get_related_subagents(self, parent: SessionInfo) -> list[SessionInfo]:
        """Get related sub-agents with caching."""
        if parent.session_id not in self._subagent_cache:
            self._subagent_cache[parent.session_id] = find_related_subagents(parent, self.subagent_sessions)
        return self._subagent_cache[parent.session_id]
    
    def _start_summary_generation(self):
        """Start background summary generation for sessions missing summaries."""
        if not HAS_ANTHROPIC:
            return
        
        # Queue sessions that need summaries (limit to first 50 for performance)
        sessions_needing_summary = [s for s in self.parent_sessions if s.needs_summary()][:50]
        if not sessions_needing_summary:
            return
        
        for session in sessions_needing_summary:
            self._summary_queue.put(session.session_id)
        
        # Start the background worker
        self._generate_summaries_background()
    
    @work(thread=True)
    def _generate_summaries_background(self):
        """Background worker to generate summaries."""
        self._summary_generating = True
        generated_count = 0
        
        while not self._summary_queue.empty():
            try:
                session_id = self._summary_queue.get_nowait()
            except Exception:
                break
            
            # Find the session
            session = next((s for s in self.parent_sessions if s.session_id == session_id), None)
            if not session or not session.needs_summary():
                continue
            
            # Generate summary
            summary = generate_summary_sync(session.first_user_prompt, session.last_assistant_response)
            if summary:
                session.summary = summary
                generated_count += 1
                # Schedule UI refresh on main thread
                self.call_from_thread(self._refresh_session_item, session_id)
        
        # Save cache when done
        if generated_count > 0:
            SummaryCache().save()
        
        self._summary_generating = False
    
    def _refresh_session_item(self, session_id: str):
        """Refresh a specific session item in the list (called from main thread)."""
        parent_list = self.query_one("#parent-list", ListView)
        for child in parent_list.children:
            if isinstance(child, ParentSessionItem) and child.session.session_id == session_id:
                child.refresh_text()
                break
    
    def _update_subagent_list(self, parent: SessionInfo):
        """Update the sub-agent list for the selected parent."""
        subagent_list = self.query_one("#subagent-list", ListView)
        subagent_list.clear()
        
        self.current_subagents = self._get_related_subagents(parent)
        
        subagent_container = self.query_one("#subagent-container")
        if self.current_subagents:
            subagent_container.remove_class("dimmed")
            for subagent in self.current_subagents:
                subagent_list.append(SubagentSessionItem(subagent, is_highlighted=True))
        else:
            subagent_container.add_class("dimmed")
    
    @on(ListView.Highlighted, "#parent-list")
    def on_parent_highlighted(self, event: ListView.Highlighted):
        """Handle parent session highlight."""
        if event.item and isinstance(event.item, ParentSessionItem):
            self.selected_session = event.item.session
            detail = self.query_one("#detail-panel", SessionDetailPanel)
            
            if self._search_mode:
                # In search mode, show search results in bottom pane
                self._update_search_results_list(event.item.session)
                # Show first result in detail if any
                if self._current_session_results:
                    detail.show_search_result(self._current_session_results[0], self._search_query)
                else:
                    detail.show_session(event.item.session, 0)
            else:
                # Normal mode - show subagents
                self._update_subagent_list(event.item.session)
                subagent_count = len(self.current_subagents)
                detail.show_session(event.item.session, subagent_count)
    
    def _update_search_results_list(self, parent: SessionInfo):
        """Update bottom pane with search results for this session."""
        subagent_list = self.query_one("#subagent-list", ListView)
        subagent_list.clear()
        
        # Get results for this parent session
        self._current_session_results = self._search_results.get(parent.session_id, [])
        
        # Also get results from related subagents
        related_subagents = self._get_related_subagents(parent)
        for subagent in related_subagents:
            if subagent.session_id in self._search_results:
                self._current_session_results.extend(self._search_results[subagent.session_id])
        
        # Update header
        self.query_one("#subagent-header", Static).update(
            f"[bold yellow]Matches[/] [dim]({len(self._current_session_results)} in this session)[/]"
        )
        
        subagent_container = self.query_one("#subagent-container")
        if self._current_session_results:
            subagent_container.remove_class("dimmed")
            for result in self._current_session_results:
                subagent_list.append(SearchResultItem(result, self._search_query))
        else:
            subagent_container.add_class("dimmed")
    
    @on(ListView.Highlighted, "#subagent-list")
    def on_subagent_highlighted(self, event: ListView.Highlighted):
        """Handle sub-agent or search result highlight."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        
        if event.item and isinstance(event.item, SearchResultItem):
            # Search result selected
            self.selected_session = event.item.result.session
            detail.show_search_result(event.item.result, self._search_query)
        elif event.item and isinstance(event.item, SubagentSessionItem):
            # Sub-agent selected
            self.selected_session = event.item.session
            detail.show_session(event.item.session)
    
    def action_switch_pane(self):
        """Switch focus between parent and sub-agent panes only (Tab)."""
        # Tab only cycles between left panes, never detail
        if self.focus_pane == "detail":
            return  # Do nothing if in detail - use shift+tab to get back
        
        if self.focus_pane == "parent":
            subagent_list = self.query_one("#subagent-list", ListView)
            # In search mode, check for search results; in normal mode, check for subagents
            has_items = self._current_session_results if self._search_mode else self.current_subagents
            if has_items:
                self.focus_pane = "subagent"
                subagent_list.focus()
                if subagent_list.index is None and len(has_items) > 0:
                    subagent_list.index = 0
            # If no items, stay on parent
        else:  # subagent
            self.focus_pane = "parent"
            self.query_one("#parent-list", ListView).focus()
    
    def action_focus_detail(self):
        """Toggle between active left pane and detail panel (Shift+Tab)."""
        if self.focus_pane == "detail":
            # Go back to the last active left pane
            has_items = self._current_session_results if self._search_mode else self.current_subagents
            if self._last_left_pane == "subagent" and has_items:
                self.focus_pane = "subagent"
                self.query_one("#subagent-list", ListView).focus()
            else:
                self.focus_pane = "parent"
                self.query_one("#parent-list", ListView).focus()
        else:
            # Remember which left pane we're leaving
            self._last_left_pane = self.focus_pane
            self.focus_pane = "detail"
            detail = self.query_one("#detail-panel", SessionDetailPanel)
            detail.focus()
    
    def action_back_to_list(self):
        """Go back to parent list (Escape)."""
        # If search input is focused, cancel search
        search_input = self.query_one("#search-input", Input)
        if search_input.has_focus:
            self._cancel_search()
            return
        
        # If in search mode, clear search first
        if self._search_mode:
            self._clear_search()
            return
        
        if self.focus_pane == "detail":
            # Go back to the last active left pane
            if self._last_left_pane == "subagent" and self.current_subagents:
                self.focus_pane = "subagent"
                self.query_one("#subagent-list", ListView).focus()
            else:
                self.focus_pane = "parent"
                self.query_one("#parent-list", ListView).focus()
        else:
            self.action_quit()
    
    def action_activate_search(self):
        """Activate search mode (/)."""
        search_input = self.query_one("#search-input", Input)
        search_input.add_class("visible")
        search_input.value = ""
        search_input.focus()
    
    def _cancel_search(self):
        """Cancel search input without executing."""
        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("visible")
        search_input.value = ""
        self.query_one("#parent-list", ListView).focus()
        self.focus_pane = "parent"
    
    def _clear_search(self):
        """Clear search results and restore normal view."""
        self._search_mode = False
        self._search_query = ""
        self._search_results = {}
        self._filtered_parents = []
        self._current_session_results = []
        
        # Hide search input
        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("visible")
        search_input.value = ""
        
        # Restore header
        self.query_one("#parent-header", Static).update("[bold]Sessions[/] [dim](newest first)[/]")
        self.query_one("#subagent-header", Static).update("[bold]Sub-agents[/] [dim](for selected session)[/]")
        
        # Repopulate parent list
        parent_list = self.query_one("#parent-list", ListView)
        parent_list.clear()
        for session in self.parent_sessions:
            subagents = self._get_related_subagents(session)
            parent_list.append(ParentSessionItem(session, len(subagents)))
        
        if self.parent_sessions:
            parent_list.index = 0
        
        parent_list.focus()
        self.focus_pane = "parent"
    
    def _execute_search(self, query: str):
        """Execute search and update display."""
        if not query.strip():
            self._cancel_search()
            return
        
        self._search_mode = True
        self._search_query = query.strip()
        
        # Search all sessions (parents and subagents)
        all_sessions = self.parent_sessions + self.subagent_sessions
        self._search_results = search_all_sessions(all_sessions, self._search_query)
        
        # Filter to parents with matches (or whose subagents have matches)
        matching_parent_ids = set()
        for session_id in self._search_results:
            # Find the session
            for p in self.parent_sessions:
                if p.session_id == session_id:
                    matching_parent_ids.add(session_id)
                    break
            # Also check if it's a subagent - include its parent
            for s in self.subagent_sessions:
                if s.session_id == session_id:
                    # Find parent by matching cwd and time
                    for p in self.parent_sessions:
                        if p.cwd == s.cwd:
                            matching_parent_ids.add(p.session_id)
        
        self._filtered_parents = [p for p in self.parent_sessions if p.session_id in matching_parent_ids]
        
        # Update UI
        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("visible")
        
        # Update headers
        total_matches = sum(len(r) for r in self._search_results.values())
        self.query_one("#parent-header", Static).update(
            f"[bold yellow]Search:[/] [white]{query}[/] [dim]({len(self._filtered_parents)} sessions, {total_matches} matches)[/]"
        )
        
        # Populate filtered parent list
        parent_list = self.query_one("#parent-list", ListView)
        parent_list.clear()
        for session in self._filtered_parents:
            match_count = len(self._search_results.get(session.session_id, []))
            parent_list.append(ParentSessionItem(session, match_count))
        
        if self._filtered_parents:
            parent_list.index = 0
        
        parent_list.focus()
        self.focus_pane = "parent"
    
    @on(Input.Submitted, "#search-input")
    def on_search_submitted(self, event: Input.Submitted):
        """Handle search input submission."""
        self._execute_search(event.value)
    
    def action_copy_command(self):
        """Copy resume command to clipboard."""
        if self.selected_session:
            cmd = f"droid --resume {self.selected_session.session_id}"
            try:
                subprocess.run(["pbcopy"], input=cmd.encode(), check=True)
                self.notify(f"Copied: {cmd}", title="Command Copied")
            except Exception:
                self.notify(f"Command: {cmd}", title="Copy Failed")
    
    def action_resume_session(self):
        """Resume the selected session."""
        if self.selected_session:
            cmd = f"droid --resume {self.selected_session.session_id}"
            self.exit(result=cmd)
    
    def _scroll_to_highlighted(self, lv: ListView):
        """Scroll ListView to ensure highlighted item is fully visible."""
        if lv.highlighted_child:
            # Use scroll_to_widget which is more reliable than scroll_visible
            lv.scroll_to_widget(lv.highlighted_child, animate=False)
    
    def action_cursor_down(self):
        """Move cursor down in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_down()
        elif self.focus_pane == "parent":
            lv = self.query_one("#parent-list", ListView)
            lv.action_cursor_down()
            self._scroll_to_highlighted(lv)
        else:
            lv = self.query_one("#subagent-list", ListView)
            lv.action_cursor_down()
            self._scroll_to_highlighted(lv)
    
    def action_cursor_up(self):
        """Move cursor up in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_up()
        elif self.focus_pane == "parent":
            lv = self.query_one("#parent-list", ListView)
            lv.action_cursor_up()
            self._scroll_to_highlighted(lv)
        else:
            lv = self.query_one("#subagent-list", ListView)
            lv.action_cursor_up()
            self._scroll_to_highlighted(lv)
    
    def _get_focused_list(self) -> Optional[ListView]:
        """Get the currently focused ListView, or None if detail pane."""
        if self.focus_pane == "parent":
            return self.query_one("#parent-list", ListView)
        elif self.focus_pane == "subagent":
            return self.query_one("#subagent-list", ListView)
        return None
    
    def action_cursor_home(self):
        """Move cursor to first item in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_home()
        else:
            lv = self._get_focused_list()
            if lv and len(lv.children) > 0:
                lv.index = 0
    
    def action_cursor_end(self):
        """Move cursor to last item in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_end()
        else:
            lv = self._get_focused_list()
            if lv and len(lv.children) > 0:
                lv.index = len(lv.children) - 1
    
    def action_cursor_page_up(self):
        """Move cursor up by a page in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_page_up()
        else:
            lv = self._get_focused_list()
            if lv and len(lv.children) > 0:
                page_size = max(1, lv.size.height - 2)
                new_index = max(0, (lv.index or 0) - page_size)
                lv.index = new_index
    
    def action_cursor_page_down(self):
        """Move cursor down by a page in focused pane."""
        if self.focus_pane == "detail":
            self.query_one("#detail-panel", SessionDetailPanel).scroll_page_down()
        else:
            lv = self._get_focused_list()
            if lv and len(lv.children) > 0:
                page_size = max(1, lv.size.height - 2)
                max_index = len(lv.children) - 1
                new_index = min(max_index, (lv.index or 0) + page_size)
                lv.index = new_index


def main():
    app = DroidSessionsBrowser()
    result = app.run()
    
    if result and result.startswith("droid --resume"):
        print(f"\n[Resuming session...]\n{result}\n")
        os.execvp("droid", result.split())


if __name__ == "__main__":
    main()
