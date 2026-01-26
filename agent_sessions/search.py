"""Search functionality for sessions."""

import json
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .models import SearchResult, Session

if TYPE_CHECKING:
    pass


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
                    content_str = str(item.get('content', ''))
                    texts.append(f"(tool_result: {content_str[:50]}...)")
            elif isinstance(item, str):
                texts.append(item)
        return " ".join(texts)
    return str(content)


def parse_search_query(query: str) -> tuple[str, dict]:
    """Parse search query with modifiers.

    Syntax:
        harness:droid           - Filter by harness
        project:api-server      - Filter by project name
        before:2024-01-15       - Sessions before date
        after:2024-01-01        - Sessions after date
        before:7d               - Sessions in last 7 days
        after:1h                - Sessions in last hour

    Returns:
        (clean_query, filters_dict)
    """
    filters = {}

    # Extract modifiers
    modifier_pattern = r'(\w+):(\S+)'
    modifiers = re.findall(modifier_pattern, query)

    for key, value in modifiers:
        key = key.lower()
        if key == 'harness':
            filters['harness'] = value.lower()
        elif key == 'project':
            filters['project'] = value
        elif key == 'before':
            filters['before'] = parse_date_value(value)
        elif key == 'after':
            filters['after'] = parse_date_value(value)

    # Remove modifiers from query
    clean_query = re.sub(modifier_pattern, '', query).strip()

    return clean_query, filters


def parse_date_value(value: str) -> datetime | None:
    """Parse a date value (ISO date or relative like '7d', '1h')."""
    # Relative time patterns
    relative_match = re.match(r'^(\d+)([dhwm])$', value.lower())
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        now = datetime.now()

        if unit == 'h':
            return now - timedelta(hours=amount)
        elif unit == 'd':
            return now - timedelta(days=amount)
        elif unit == 'w':
            return now - timedelta(weeks=amount)
        elif unit == 'm':
            return now - timedelta(days=amount * 30)

    # ISO date format
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass

    # Common date formats
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%m-%d', '%m/%d']:
        try:
            dt = datetime.strptime(value, fmt)
            # If no year, use current year
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt
        except ValueError:
            continue

    return None


def search_session_file(session: Session, query: str, max_results: int = 50) -> list[SearchResult]:
    """Search a session's file for query matches, returning results with context."""
    results = []
    query_lower = query.lower()

    # Only handle JSONL format (Droid, Claude Code)
    if not session.raw_path.suffix == ".jsonl":
        return results

    try:
        with open(session.raw_path) as f:
            all_messages = []
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    # Handle both Droid format (type=message) and Claude Code format (type=user/assistant)
                    msg_type = data.get("type")
                    if msg_type == "message":
                        msg = data.get("message", {})
                        role = msg.get("role", "")
                        if role in ("user", "assistant"):
                            content = extract_text_content(msg.get("content", ""), text_only=(role == "user"))
                            if content and "<system-reminder>" not in content[:100]:
                                all_messages.append((role, content))
                    elif msg_type in ("user", "assistant"):
                        msg = data.get("message", {})
                        role = msg.get("role", msg_type)
                        if role in ("user", "assistant"):
                            content = extract_text_content(msg.get("content", ""), text_only=(role == "user"))
                            if content and "<system-reminder>" not in content[:100]:
                                all_messages.append((role, content))
                except json.JSONDecodeError:
                    continue

            # Search through messages
            for idx, (role, content) in enumerate(all_messages):
                if query_lower in content.lower():
                    lines = content.split("\n")
                    for line_num, line in enumerate(lines):
                        if query_lower in line.lower():
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


def search_sessions(sessions: list[Session], query: str) -> dict[str, list[SearchResult]]:
    """Search all sessions and return results grouped by session ID."""
    results_by_session = {}

    for session in sessions:
        results = search_session_file(session, query)
        if results:
            results_by_session[session.id] = results

    return results_by_session


class SearchEngine:
    """Search engine with filtering and scoping capabilities."""

    def __init__(self, sessions: list[Session]):
        self.sessions = sessions
        self._index_built = False

    def search(
        self,
        query: str,
        harness: str | None = None,
        project: str | None = None,
        before: datetime | None = None,
        after: datetime | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Search sessions with optional filters.

        Args:
            query: Search query string (may contain modifiers like harness:, project:)
            harness: Filter to specific harness (e.g., "droid", "claude-code")
            project: Filter to specific project name
            before: Only sessions modified before this date
            after: Only sessions modified after this date
        """
        # Parse query for inline modifiers
        clean_query, parsed_filters = parse_search_query(query)

        # Inline modifiers override explicit parameters
        harness = parsed_filters.get('harness', harness)
        project = parsed_filters.get('project', project)
        before = parsed_filters.get('before', before)
        after = parsed_filters.get('after', after)

        # Apply filters
        filtered = self.sessions

        if harness:
            filtered = [s for s in filtered if s.harness == harness]

        if project:
            filtered = [s for s in filtered if project.lower() in s.project_name.lower()]

        if before:
            filtered = [s for s in filtered if s.modified_time and s.modified_time < before]

        if after:
            filtered = [s for s in filtered if s.modified_time and s.modified_time > after]

        # If no search text, return empty (filters alone don't search)
        if not clean_query:
            return {}

        return search_sessions(filtered, clean_query)

    def get_matching_sessions(self, results: dict[str, list[SearchResult]]) -> list[Session]:
        """Get unique sessions from search results, ordered by match count."""
        session_counts = {}
        session_map = {}

        for session_id, matches in results.items():
            session_counts[session_id] = len(matches)
            if matches:
                session_map[session_id] = matches[0].session

        # Sort by match count descending
        sorted_ids = sorted(session_counts.keys(), key=lambda x: session_counts[x], reverse=True)
        return [session_map[sid] for sid in sorted_ids if sid in session_map]
