"""Caching for session summaries and metadata."""

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import importlib.util

HAS_OPENAI = importlib.util.find_spec("openai") is not None

# Default cache locations
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "agent-sessions" / "summaries.json"
METADATA_CACHE_PATH = Path.home() / ".cache" / "agent-sessions" / "metadata.json"
SUMMARY_MODEL = "gpt-5.2"


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
    """Generate a summary using GPT-5 nano (synchronous)."""
    if not HAS_OPENAI:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        context = f"""SESSION START (user request):
{first_prompt[:1500]}

SESSION END (final assistant response):
{last_response[:1500]}"""

        response = client.chat.completions.create(
            model=SUMMARY_MODEL,
            max_completion_tokens=2000,
            messages=[
                {
                    "role": "system",
                    "content": """You summarize AI coding sessions in 8-12 words.

Rules:
- Describe the PURPOSE or OUTCOME, not the steps taken
- Answer "what was accomplished" not "what actions were performed"
- Use past tense verbs
- Be specific: name the feature, bug, system, or domain
- Never mention generic actions like "updated files", "ran commands", "fixed issues", "reconnected", "reviewed code"
- Never mention the AI assistant, todos, or session mechanics
- No quotes, no punctuation at end

Bad: "Updated todos and reconnected the browser" (generic steps)
Bad: "Reviewed documentation and prepared settings" (vague)
Good: "Built real-time WebSocket chat with typing indicators"
Good: "Migrated auth from JWT to session cookies with CSRF protection"
Good: "Diagnosed OOM crash caused by unbounded worker queue"
"""
                },
                {
                    "role": "user",
                    "content": f"""{context}

Summary:"""
                }
            ]
        )

        content = response.choices[0].message.content
        if not content:
            generate_summary_sync._last_error = f"Empty response from {SUMMARY_MODEL}"
            return None
        summary = content.strip().strip('"\'').rstrip('.')
        return summary[:80] if summary else None

    except Exception as e:
        import traceback
        err_detail = f"{type(e).__name__}: {e}"
        logging.getLogger(__name__).warning(f"Summary generation failed: {err_detail}")
        generate_summary_sync._last_error = err_detail
        return None
