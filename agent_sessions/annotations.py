"""Annotation file I/O for agent-sessions.

Annotations live at ~/.local/share/agent-sessions/annotations/{session_id}.json

Each file contains a JSON array of annotation objects:
    [{"ts": "2026-03-15T12:00:00Z", "type": "tag", "value": "debug", "source": "manual"}, ...]
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ANNOTATIONS_DIR = Path.home() / ".local" / "share" / "agent-sessions" / "annotations"


def get_annotations_dir() -> Path:
    """Return annotations directory, creating if needed."""
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return ANNOTATIONS_DIR


def load_annotations(session_id: str) -> list[dict]:
    """Load annotations for a session from disk. Returns empty list if no file.

    Handles both formats:
      - Hook format: {"session_id": "...", "annotations": [...]}
      - Plain list: [...]
    """
    path = ANNOTATIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("annotations", [])
        return []
    except (json.JSONDecodeError, OSError):
        return []


def save_annotation(
    session_id: str,
    annotation_type: str,
    value: str,
    source: str = "manual",
) -> dict:
    """Append a single annotation to a session's file.

    Returns the annotation dict. Creates the file if it doesn't exist.
    Appends to existing annotations array. Uses ISO 8601 UTC timestamp.
    """
    get_annotations_dir()
    path = ANNOTATIONS_DIR / f"{session_id}.json"

    existing = load_annotations(session_id)
    annotation = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": annotation_type,
        "value": value,
        "source": source,
    }
    existing.append(annotation)
    data = {"session_id": session_id, "annotations": existing}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return annotation


def get_all_annotation_files() -> list[Path]:
    """Glob all annotation JSON files."""
    if not ANNOTATIONS_DIR.exists():
        return []
    return sorted(ANNOTATIONS_DIR.glob("*.json"))


def get_annotation_file_mtime(session_id: str) -> Optional[float]:
    """Return mtime of annotation file, or None if doesn't exist."""
    path = ANNOTATIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    return path.stat().st_mtime
