"""End-to-end Pilot-driven tests for the in-transcript find UX.

Drives the live Textual app via Pilot but stubs out the disk-sync work so
we can exercise the find bar deterministically against a known transcript.
"""

from pathlib import Path

import pytest
from textual.widgets import Input

from agent_sessions.app import AgentSessionsBrowser
from agent_sessions.models import Session
from agent_sessions.ui.widgets import SessionDetailPanel, TranscriptFindBar


def _fake_session() -> Session:
    return Session(
        id="test",
        harness="codex",
        raw_path=Path("/tmp/x"),
        project_path=Path("/tmp"),
        project_name="proj",
        first_prompt="",
        last_response="",
    )


@pytest.mark.asyncio
async def test_find_bar_lifecycle_against_synthetic_transcript(monkeypatch):
    """Open transcript → press / → type → n/N navigate → Esc closes."""

    # Skip the slow on-mount disk sync; we'll set up the panel state by hand.
    monkeypatch.setattr(
        AgentSessionsBrowser, "_load_sessions_background", lambda self: None
    )

    app = AgentSessionsBrowser()
    async with app.run_test() as pilot:
        await pilot.pause()

        detail = app.query_one("#detail-panel", SessionDetailPanel)
        detail.display = True

        # Simulate a finished transcript: mount the TranscriptArea and seed text.
        session = _fake_session()
        detail.show_full_transcript_start(session, total=3)
        # Build a transcript with three known matches of "needle"
        detail._transcript_buf = (
            "intro line\n"
            "first needle here\n"
            "filler line\n"
            "second NEEDLE in caps\n"
            "third needle and a needle on same line\n"
        )
        detail.show_full_transcript_end()
        await pilot.pause()

        # Sanity: still in transcript mode and ready
        assert detail._in_transcript_mode is True
        assert detail._transcript_ready is True
        assert detail._find_bar is None

        # Activate find via the app action (simulates `/` from transcript)
        app.action_activate_search()
        await pilot.pause()

        assert detail._find_bar is not None
        assert isinstance(detail._find_bar, TranscriptFindBar)
        assert detail._find_bar.has_focus

        # Type the query — Input.Changed should fire and update matches
        detail._find_bar.value = "needle"
        await pilot.pause()
        assert len(detail._find_matches) == 4  # 4 matches (case-insensitive)
        assert detail._find_index == 0
        # Border title shows match counter
        assert "1/4" in str(detail._find_bar.border_title)

        # Next match
        app.action_transcript_find_next()
        assert detail._find_index == 1
        # Prev wraps from 0 → last
        app.action_transcript_find_prev()
        app.action_transcript_find_prev()
        assert detail._find_index == 3  # wrapped past 0 to last
        # Forward wraps too
        app.action_transcript_find_next()
        assert detail._find_index == 0

        # Close find with Esc-equivalent action
        app.action_transcript_find_close()
        await pilot.pause()
        assert detail._find_bar is None
        assert detail._find_matches == []


@pytest.mark.asyncio
async def test_find_blocked_until_transcript_ready(monkeypatch):
    """`/` while transcript is still loading should not mount the find bar."""
    monkeypatch.setattr(
        AgentSessionsBrowser, "_load_sessions_background", lambda self: None
    )

    app = AgentSessionsBrowser()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = app.query_one("#detail-panel", SessionDetailPanel)
        detail.display = True

        session = _fake_session()
        # Start streaming but never end → not ready
        detail.show_full_transcript_start(session, total=1)
        detail._transcript_buf = "partial"
        # Note: NOT calling show_full_transcript_end → _transcript_ready stays False

        app.action_activate_search()
        await pilot.pause()
        # Find bar should NOT have mounted
        assert detail._find_bar is None
        # And the global search input should also not be visible (we routed
        # away into the transcript-find branch which then bailed on not-ready)
        search_input = app.query_one("#search-input", Input)
        assert "visible" not in search_input.classes


@pytest.mark.asyncio
async def test_no_matches_status(monkeypatch):
    monkeypatch.setattr(
        AgentSessionsBrowser, "_load_sessions_background", lambda self: None
    )
    app = AgentSessionsBrowser()
    async with app.run_test() as pilot:
        await pilot.pause()
        detail = app.query_one("#detail-panel", SessionDetailPanel)
        detail.display = True

        session = _fake_session()
        detail.show_full_transcript_start(session, total=1)
        detail._transcript_buf = "alpha beta gamma\n"
        detail.show_full_transcript_end()
        await pilot.pause()

        app.action_activate_search()
        await pilot.pause()
        detail._find_bar.value = "zzz"
        await pilot.pause()
        assert detail._find_matches == []
        assert "no matches" in str(detail._find_bar.border_title).lower()
