"""Cursor session provider."""

import json
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..cache import MetadataCache, SummaryCache, compute_content_hash
from ..models import Session
from . import register_provider
from .base import SessionProvider


# Cursor stores data in VS Code-style SQLite databases
CURSOR_DATA_DIR = Path.home() / "Library" / "Application Support" / "Cursor"
GLOBAL_STORAGE_DB = CURSOR_DATA_DIR / "User" / "globalStorage" / "state.vscdb"
WORKSPACE_STORAGE_DIR = CURSOR_DATA_DIR / "User" / "workspaceStorage"

# Temp DB copy for when Cursor has the DB locked
_temp_db_path: Optional[Path] = None


def _get_db_connection():
    """Get a connection to the Cursor database, copying if necessary."""
    global _temp_db_path

    if not GLOBAL_STORAGE_DB.exists():
        return None

    # Try direct connection first (read-only)
    try:
        conn = sqlite3.connect(f"file:{GLOBAL_STORAGE_DB}?mode=ro", uri=True)
        conn.execute("SELECT 1")  # Test connection
        return conn
    except sqlite3.Error:
        pass

    # DB is locked, copy to temp location
    try:
        if _temp_db_path is None or not Path(_temp_db_path).exists():
            fd, _temp_db_path = tempfile.mkstemp(suffix=".vscdb")
            import os
            os.close(fd)
            shutil.copy(GLOBAL_STORAGE_DB, _temp_db_path)

        return sqlite3.connect(_temp_db_path)
    except (IOError, sqlite3.Error):
        return None


def extract_text_from_richtext(richtext_json: str) -> str:
    """Extract plain text from Cursor's Lexical richText format."""
    try:
        data = json.loads(richtext_json)
        texts = []

        def extract_text_nodes(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
                elif node.get("type") == "mention":
                    texts.append(f"@{node.get('mentionName', '')}")
                children = node.get("children", [])
                for child in children:
                    extract_text_nodes(child)
            elif isinstance(node, list):
                for item in node:
                    extract_text_nodes(item)

        extract_text_nodes(data.get("root", {}))
        return " ".join(texts).strip()
    except (json.JSONDecodeError, TypeError):
        return ""


def get_workspace_path_from_hash(ws_hash: str) -> Optional[Path]:
    """Try to find the workspace path from workspace.json."""
    ws_dir = WORKSPACE_STORAGE_DIR / ws_hash
    workspace_json = ws_dir / "workspace.json"
    if workspace_json.exists():
        try:
            with open(workspace_json) as f:
                data = json.load(f)
                folder = data.get("folder")
                if folder:
                    # folder is a URI like "file:///Users/erik/project"
                    if folder.startswith("file://"):
                        return Path(folder[7:])
        except (json.JSONDecodeError, IOError):
            pass
    return None


@register_provider
class CursorProvider(SessionProvider):
    """Provider for Cursor AI sessions."""

    name = "cursor"
    display_name = "Cursor"
    icon = "⌘"
    color = "blue"

    def get_sessions_dir(self) -> Path:
        return CURSOR_DATA_DIR

    def discover_session_files(self) -> list[Path]:
        """Return list of composer session IDs as virtual paths."""
        if not GLOBAL_STORAGE_DB.exists():
            return []

        files = []
        conn = _get_db_connection()
        if conn is None:
            return files

        try:
            cursor = conn.cursor()

            # Get all background composer sessions
            cursor.execute("""
                SELECT key FROM cursorDiskKV
                WHERE key LIKE 'backgroundComposerModalInputData:%'
            """)

            for (key,) in cursor.fetchall():
                # Create a virtual path for each session
                session_id = key.replace("backgroundComposerModalInputData:", "")
                # Use a virtual path that includes the session ID
                virtual_path = CURSOR_DATA_DIR / "sessions" / f"{session_id}.cursor"
                files.append(virtual_path)

            conn.close()
        except sqlite3.Error:
            pass

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
            extra={},
        )

    def parse_session(self, path: Path) -> Session | None:
        """Parse a Cursor composer session."""
        session_id = path.stem

        # Check metadata cache
        cache = MetadataCache()
        # Use DB mtime as cache key since we can't stat virtual files
        try:
            db_mtime = GLOBAL_STORAGE_DB.stat().st_mtime
        except OSError:
            return None

        cache_key = CURSOR_DATA_DIR / "sessions" / f"{session_id}.cursor"
        cached = cache.get(cache_key, db_mtime)
        if cached:
            return self._session_from_cache(path, cached)

        # Parse from database
        conn = _get_db_connection()
        if conn is None:
            return None

        first_prompt = ""
        last_response = ""
        project_path = Path.home()
        project_name = "Cursor"
        title = ""
        model = "unknown"
        created_time = None
        modified_time = None

        try:
            cursor = conn.cursor()

            # Get composer data
            key = f"backgroundComposerModalInputData:{session_id}"
            cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,))
            row = cursor.fetchone()

            if row:
                try:
                    data = json.loads(row[0])
                    composer_data = data.get("composerData", {})

                    # Extract prompt from richText
                    richtext = composer_data.get("richText", "")
                    first_prompt = extract_text_from_richtext(richtext)

                    # Try to get project path from file references
                    if "fsPath" in richtext:
                        match = re.search(r'"fsPath":"([^"]+)"', richtext)
                        if match:
                            file_path = Path(match.group(1))
                            # Find project root (go up until no more common project indicators)
                            for parent in file_path.parents:
                                if (parent / ".git").exists() or (parent / "package.json").exists():
                                    project_path = parent
                                    break
                            else:
                                project_path = file_path.parent

                    project_name = project_path.name

                except (json.JSONDecodeError, TypeError):
                    pass

            # Get cached details for timestamp and response
            details_key = f"bcCachedDetails:{session_id}"
            cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (details_key,))
            details_row = cursor.fetchone()

            if details_row:
                try:
                    details = json.loads(details_row[0])
                    # Extract model and timestamps if available
                    if details.get("model"):
                        model = details.get("model")
                    if details.get("lastResponse"):
                        last_response = details.get("lastResponse", "")[:2000]
                except (json.JSONDecodeError, TypeError):
                    pass

            conn.close()

        except sqlite3.Error:
            return None

        if not first_prompt:
            return None

        # Generate title from first prompt
        first_line = first_prompt.split('\n')[0].strip()
        title = first_line[:80] if first_line else "Cursor Session"

        # Use DB modification time
        modified_time = datetime.fromtimestamp(db_mtime)

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
            "last_prompt": first_prompt[:2000],  # Cursor doesn't have separate last prompt
            "last_response": last_response,
            "created_time": None,
            "modified_time": modified_time.isoformat() if modified_time else None,
            "is_child": False,
            "child_type": "",
            "model": model,
            "content_hash": content_hash,
        }
        cache.set(cache_key, db_mtime, metadata)

        return Session(
            id=session_id,
            harness=self.name,
            raw_path=path,
            project_path=project_path,
            project_name=project_name,
            title=title,
            first_prompt=first_prompt,
            last_prompt=first_prompt,
            last_response=last_response,
            created_time=created_time,
            modified_time=modified_time,
            is_child=False,
            child_type="",
            model=model,
            summary=summary,
            content_hash=content_hash,
            extra={},
        )

    def get_resume_command(self, session: Session) -> str:
        # Cursor doesn't have a CLI resume command
        return f"# Open Cursor and restore session {session.id}"

    def find_children(self, parent: Session, all_sessions: list[Session]) -> list[Session]:
        # Cursor doesn't have sub-agent concept
        return []
