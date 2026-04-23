"""Tests for indexing behavior."""

import os
from datetime import datetime, timedelta
from pathlib import Path

from agent_sessions.index.database import SessionDatabase
from agent_sessions.index.indexer import SessionIndexer
from agent_sessions.models import Session
from agent_sessions.providers.base import SessionProvider


class _BackfillTestProvider(SessionProvider):
    """Minimal provider used to verify startup backfill behavior."""

    name = "backfill-test"
    display_name = "Backfill Test"
    icon = "T"
    color = "white"

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = sessions_dir

    def get_sessions_dir(self) -> Path:
        return self._sessions_dir

    def discover_session_files(self) -> list[Path]:
        return sorted(self._sessions_dir.glob("*.jsonl"))

    def parse_session(self, path: Path) -> Session | None:
        ts = datetime.fromtimestamp(path.stat().st_mtime)
        return Session(
            id=path.stem,
            harness=self.name,
            raw_path=path,
            project_path=self._sessions_dir,
            project_name="backfill-project",
            first_prompt=f"Prompt for {path.stem}",
            last_prompt=f"Prompt for {path.stem}",
            last_response=f"Response for {path.stem}",
            created_time=ts,
            modified_time=ts,
        )

    def get_resume_command(self, session: Session) -> str:
        return f"backfill --resume {session.id}"


def test_incremental_update_backfills_missing_provider_sessions_even_if_old(tmp_path):
    """A provider with disk backlog should bypass the age cutoff once."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    old_time = (datetime.now() - timedelta(days=30)).timestamp()
    for name in ("session-1", "session-2", "session-3"):
        path = sessions_dir / f"{name}.jsonl"
        path.write_text("{}\n")
        os.utime(path, (old_time, old_time))

    SessionDatabase.reset_instance()
    db = SessionDatabase(tmp_path / "sessions.db")

    # Seed the DB with just one row so the provider looks partially indexed.
    db.upsert_session(
        session_id="session-1",
        harness="backfill-test",
        timestamp=int(old_time),
        project_path=str(sessions_dir),
        project_name="backfill-project",
        first_prompt_preview="Prompt for session-1",
        last_response_preview="Response for session-1",
        file_path=str(sessions_dir / "session-1.jsonl"),
        file_mtime=int(old_time),
        indexed_at=int(datetime.now().timestamp()),
    )

    indexer = SessionIndexer(db, [_BackfillTestProvider(sessions_dir)])
    stats = indexer.incremental_update(max_age_hours=48)

    assert stats["sessions_indexed"] == 2
    assert db.count_sessions("backfill-test") == 3

    SessionDatabase.reset_instance()
