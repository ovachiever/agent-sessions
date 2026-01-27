"""Caching for session summaries and metadata."""

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# Default cache locations
DEFAULT_CACHE_PATH = Path.home() / ".factory" / "session-summaries.json"
METADATA_CACHE_PATH = Path.home() / ".cache" / "agent-sessions" / "metadata.json"
HAIKU_MODEL = "claude-haiku-4-5-20251001"


class MetadataCache:
    """Cache for parsed session metadata to speed up startup.

    Stores session metadata keyed by file path, with mtime for invalidation.
    """

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
        if METADATA_CACHE_PATH.exists():
            try:
                with open(METADATA_CACHE_PATH) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def save(self):
        """Save cache to disk if dirty."""
        with self._lock:
            if not self._dirty:
                return
            try:
                METADATA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(METADATA_CACHE_PATH, "w") as f:
                    json.dump(self._data, f)
                self._dirty = False
            except IOError:
                pass

    def get(self, file_path: Path, mtime: float) -> Optional[dict]:
        """Get cached metadata if mtime matches."""
        key = str(file_path)
        with self._lock:
            entry = self._data.get(key)
            if entry and entry.get("mtime") == mtime:
                return entry.get("metadata")
            return None

    def set(self, file_path: Path, mtime: float, metadata: dict):
        """Cache session metadata."""
        key = str(file_path)
        with self._lock:
            self._data[key] = {"mtime": mtime, "metadata": metadata}
            self._dirty = True


class SummaryCache:
    """Thread-safe cache for AI-generated session summaries."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, cache_path: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache_path = cache_path or DEFAULT_CACHE_PATH
            cls._instance._data = {}
            cls._instance._dirty = False
            cls._instance._load()
        return cls._instance

    def _load(self):
        """Load cache from disk."""
        if self._cache_path.exists():
            try:
                with open(self._cache_path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}

    def save(self):
        """Save cache to disk if dirty."""
        with self._lock:
            if not self._dirty:
                return
            try:
                self._cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._cache_path, "w") as f:
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


def compute_content_hash(first_prompt: str, last_response: str) -> str:
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
