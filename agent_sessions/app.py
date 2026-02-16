"""Agent Sessions Browser TUI Application."""

import logging
import os
import subprocess
from queue import Queue
from typing import Optional

logger = logging.getLogger(__name__)

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, ListView, LoadingIndicator, Static

from .cache import MetadataCache, generate_summary_sync, HAS_OPENAI
from .index import SessionDatabase, SessionIndexer, HybridSearch
from .models import Session
from .providers import get_available_providers, get_provider
from .search import search_sessions
from .ui import (
    APP_CSS,
    ParentSessionItem,
    SessionDetailPanel,
    SubagentSessionItem,
)


# Additional CSS for filter bar
FILTER_CSS = """
#filter-bar {
    height: 1;
    padding: 0 1;
    background: $surface;
}

#filter-bar.hidden {
    display: none;
}

#loading-container {
    width: 100%;
    height: 100%;
    align: center middle;
    display: none;
}

#loading-container.visible {
    display: block;
}

#loading-status {
    text-align: center;
    width: 100%;
    padding: 1;
    color: $text-muted;
}

#loading-indicator {
    width: 100%;
    height: 3;
}
"""


class AgentSessionsBrowser(App):
    """TUI for browsing AI coding sessions with split parent/sub-agent panes."""

    CSS = APP_CSS + FILTER_CSS

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("enter", "copy_command", "Copy"),
        Binding("r", "resume_session", "Resume"),
        Binding("tab", "switch_pane", "Tab: Lists", priority=True),
        Binding("shift+tab", "focus_detail", "Detail", priority=True),
        Binding("escape", "back_to_list", "Back"),
        Binding("slash", "activate_search", "Search"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("i", "reindex", "reIndex"),
        Binding("t", "show_all_messages", "Transcript"),
        Binding("y", "copy_transcript", "Copy All", show=False),
        Binding("ctrl+a", "select_all_transcript", "Select All", show=False, priority=True),
        Binding("c", "copy_visible_message", "Copy Msg", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("home", "cursor_home", "Home", show=False, priority=True),
        Binding("end", "cursor_end", "End", show=False, priority=True),
        Binding("pageup", "cursor_page_up", "PgUp", show=False, priority=True),
        Binding("pagedown", "cursor_page_down", "PgDn", show=False, priority=True),
    ]

    def __init__(self, harness_filter: str | None = None, project_filter: str | None = None):
        super().__init__()
        self.initial_harness_filter = harness_filter
        self.project_filter = project_filter

        # Active harness filter (None = all)
        self.active_harness_filter: str | None = harness_filter

        # Available providers (populated on mount)
        self.available_providers: list = []

        self.all_sessions: list[Session] = []
        self.parent_sessions: list[Session] = []
        self.child_sessions: list[Session] = []
        self.current_children: list[Session] = []
        self.selected_session: Optional[Session] = None
        self.focus_pane = "parent"
        self._last_left_pane = "parent"
        self._children_cache: dict[str, list[Session]] = {}

        # Search state
        self._search_mode = False
        self._search_query = ""
        self._search_scores: dict[str, float] = {}
        self._filtered_parents: list[Session] = []
        self._search_matching_children: list[Session] = []

        # Summary generation state
        self._summary_queue: Queue = Queue()
        self._summary_generating = False

        # Database and indexing
        self.db = SessionDatabase()
        self.search_engine = HybridSearch(self.db)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left-container"):
                yield Static("", id="filter-bar")
                yield Input(placeholder="Search sessions... (Enter to search, Escape to cancel)", id="search-input")
                with Vertical(id="parent-container"):
                    yield Static("[bold]Sessions[/] [dim](newest first)[/]", id="parent-header", classes="list-header")
                    yield ListView(id="parent-list")
                with Vertical(id="subagent-container"):
                    yield Static("[bold]Sub-agents[/] [dim](for selected session)[/]", id="subagent-header", classes="list-header")
                    yield ListView(id="subagent-list")
            with Vertical(id="detail-container"):
                with Vertical(id="loading-container"):
                    yield LoadingIndicator(id="loading-indicator")
                    yield Static("Syncing sessions...", id="loading-status")
                yield SessionDetailPanel(id="detail-panel")
        yield Footer()

    def on_mount(self):
        """Load sessions when app mounts."""
        self.title = "Agent Sessions Browser"

        # Get available providers and create indexer
        self.available_providers = get_available_providers()
        self.indexer = SessionIndexer(self.db, self.available_providers)

        # Show loading indicator
        self.query_one("#loading-container").add_class("visible")
        self.query_one("#detail-panel").display = False

        # Update filter bar (will show 0 sessions initially)
        self._update_filter_bar()

        self._load_sessions_background()

    def _set_loading_status(self, message: str):
        """Update loading status text (must be called from main thread)."""
        self.query_one("#loading-status", Static).update(message)

    @work(thread=True)
    def _load_sessions_background(self):
        """Auto-index new/changed sessions, then load from DB."""
        try:
            self.call_from_thread(self._set_loading_status, "Checking for new sessions...")
            stats = self.indexer.incremental_update(max_age_hours=48)
            if stats["sessions_indexed"] > 0:
                self.call_from_thread(
                    self._set_loading_status,
                    f"Indexed {stats['sessions_indexed']} new sessions, loading..."
                )
            else:
                self.call_from_thread(self._set_loading_status, "Loading sessions...")
        except Exception:
            self.call_from_thread(self._set_loading_status, "Loading sessions...")
        self._load_sessions()
        MetadataCache().save()
        self.call_from_thread(self._on_sessions_loaded)

    def _on_sessions_loaded(self):
        """Called when background session loading completes."""
        # Hide loading, show detail panel
        self.query_one("#loading-container").remove_class("visible")
        self.query_one("#detail-panel").display = True

        self._update_filter_bar()
        self._populate_parent_list()

        parent_list = self.query_one("#parent-list", ListView)

        if not self.parent_sessions:
            detail = self.query_one("#detail-panel", SessionDetailPanel)
            text = Text()
            text.append("No sessions found!", style="bold red")
            text.append("\n\nChecked providers:\n")
            for provider in self.available_providers:
                text.append(f"  {provider.icon} {provider.display_name}: {provider.get_sessions_dir()}\n")
            detail.update(text)
        else:
            parent_list.index = 0
            parent_list.focus()
            self._start_summary_generation()

    def _update_filter_bar(self):
        """Update the filter bar display."""
        filter_bar = self.query_one("#filter-bar", Static)

        if len(self.available_providers) <= 1:
            filter_bar.add_class("hidden")
            return

        filter_bar.remove_class("hidden")

        text = Text()
        text.append("Filter: ", style="dim")

        # "All" option
        if self.active_harness_filter is None:
            text.append("[●All] ", style="bold cyan")
        else:
            text.append("[○All] ", style="dim")

        # Provider options
        for provider in self.available_providers:
            if self.active_harness_filter == provider.name:
                text.append(f"[●{provider.icon}{provider.display_name}] ", style=f"bold {provider.color}")
            else:
                text.append(f"[○{provider.icon}{provider.display_name}] ", style="dim")

        # Session count
        count = len(self.parent_sessions)
        text.append(f" | {count} sessions", style="dim")

        filter_bar.update(text)

    def _load_sessions(self):
        """Load sessions from database."""
        # Load from database (includes summaries from DB summaries table via JOIN)
        self.all_sessions = self.db.get_all_sessions()

        # Migrate summaries from old JSON cache into DB for sessions missing them
        self._migrate_json_summaries()

        # Apply project filter
        if self.project_filter:
            self.all_sessions = [s for s in self.all_sessions if self.project_filter.lower() in s.project_name.lower()]

        # Sort by modified time
        self.all_sessions.sort(key=lambda s: s.modified_time or s.created_time, reverse=True)

        # Apply harness filter and separate parents/children
        self._apply_harness_filter()

    def _migrate_json_summaries(self):
        """Migrate summaries from old JSON cache files into the DB summaries table.

        Checks both old (~/.factory/session-summaries.json) and new
        (~/.cache/agent-sessions/summaries.json) cache paths.
        """
        import json
        import time as _time
        from pathlib import Path

        session_ids = {s.id for s in self.all_sessions}
        sessions_by_id = {s.id: s for s in self.all_sessions}
        migrated = 0

        cache_paths = [
            Path.home() / ".factory" / "session-summaries.json",
            Path.home() / ".cache" / "agent-sessions" / "summaries.json",
        ]

        for cache_path in cache_paths:
            if not cache_path.exists():
                continue
            try:
                with open(cache_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            for session_id, entry in data.items():
                if session_id not in session_ids:
                    continue
                session = sessions_by_id[session_id]
                if session.summary:
                    continue
                summary_text = entry.get("summary")
                if not summary_text:
                    continue

                session.summary = summary_text
                self.db.upsert_summary(
                    session_id=session_id,
                    summary=summary_text,
                    model="gpt-5.2",
                    content_hash=entry.get("hash", ""),
                    created_at=int(_time.time()),
                )
                migrated += 1

        if migrated > 0:
            logger.info(f"Migrated {migrated} summaries from JSON cache to DB")

    def _apply_harness_filter(self):
        """Apply current harness filter to sessions."""
        if self.active_harness_filter:
            filtered = [s for s in self.all_sessions if s.harness == self.active_harness_filter]
        else:
            filtered = self.all_sessions

        self.parent_sessions = [s for s in filtered if not s.is_child]
        self.child_sessions = [s for s in filtered if s.is_child]

        # Clear children cache when filter changes
        self._children_cache = {}

    def _populate_parent_list(self):
        """Populate the parent list with sessions."""
        parent_list = self.query_one("#parent-list", ListView)

        # Limit displayed items for performance (can scroll to load more)
        MAX_DISPLAY = 500
        sessions_to_show = self.parent_sessions[:MAX_DISPLAY]

        # Pre-compute child counts for display
        # Use fast heuristic matching (same as _get_related_children)
        child_counts = self._compute_child_counts(sessions_to_show)

        # Batch mount all items at once for performance
        parent_list.clear()
        items = [
            ParentSessionItem(session, child_count=child_counts.get(session.id, 0))
            for session in sessions_to_show
        ]
        parent_list.mount(*items)

    def _compute_child_counts(self, parents: list[Session]) -> dict[str, int]:
        """Pre-compute child counts for a list of parent sessions.

        Uses fast heuristic matching by project_path and time proximity.
        Returns dict mapping parent session ID to child count.
        """
        from datetime import timedelta

        counts: dict[str, int] = {}

        # Group children by (harness, project_path) for faster lookup
        children_by_key: dict[tuple[str, str], list[Session]] = {}
        for child in self.child_sessions:
            key = (child.harness, str(child.project_path))
            if key not in children_by_key:
                children_by_key[key] = []
            children_by_key[key].append(child)

        for parent in parents:
            if parent.is_child or not parent.modified_time:
                counts[parent.id] = 0
                continue

            # Check cache first
            if parent.id in self._children_cache:
                counts[parent.id] = len(self._children_cache[parent.id])
                continue

            # Look up children by same harness and project
            key = (parent.harness, str(parent.project_path))
            potential_children = children_by_key.get(key, [])

            if not potential_children:
                counts[parent.id] = 0
                continue

            # Time window varies by harness
            if parent.harness == "opencode":
                time_window = timedelta(hours=24)
            else:
                time_window = timedelta(hours=2)

            # Count children within time window
            related = []
            for child in potential_children:
                child_time = child.modified_time or child.created_time
                if child_time and abs((child_time - parent.modified_time).total_seconds()) < time_window.total_seconds():
                    related.append(child)

            # Cache the result
            related.sort(key=lambda s: s.created_time or s.modified_time)
            self._children_cache[parent.id] = related
            counts[parent.id] = len(related)

        return counts

    def _get_related_children(self, parent: Session) -> list[Session]:
        """Get related child sessions using fast heuristic matching.

        Matches children by project_path and time proximity.
        Time window varies by harness (OpenCode uses 24h, others use 2h).
        """
        if parent.id not in self._children_cache:
            from datetime import timedelta

            related = []
            if parent.modified_time:
                # OpenCode sub-agents run throughout a workday, need longer window
                if parent.harness == "opencode":
                    time_window = timedelta(hours=24)
                else:
                    time_window = timedelta(hours=2)

                for child in self.child_sessions:
                    # Must be same harness and project
                    if child.harness != parent.harness:
                        continue
                    if child.project_path != parent.project_path:
                        continue
                    # Check time proximity
                    child_time = child.modified_time or child.created_time
                    if child_time and abs((child_time - parent.modified_time).total_seconds()) < time_window.total_seconds():
                        related.append(child)

            # Sort by time
            related.sort(key=lambda s: s.created_time or s.modified_time)
            self._children_cache[parent.id] = related

        return self._children_cache[parent.id]

    def action_cycle_filter(self):
        """Cycle through harness filters."""
        if len(self.available_providers) <= 1:
            return

        # Build filter options: None (all), then each provider
        options = [None] + [p.name for p in self.available_providers]

        # Find current index
        try:
            current_idx = options.index(self.active_harness_filter)
        except ValueError:
            current_idx = 0

        # Move to next
        next_idx = (current_idx + 1) % len(options)
        self.active_harness_filter = options[next_idx]

        # Re-filter and refresh
        self._apply_harness_filter()
        self._update_filter_bar()
        self._populate_parent_list()

        # Update header
        total = len(self.parent_sessions)
        shown = min(total, 500)
        count_text = f"{shown}/{total}" if total > 500 else str(total)

        if self.active_harness_filter:
            provider = get_provider(self.active_harness_filter)
            if provider:
                self.query_one("#parent-header", Static).update(
                    f"[bold]{provider.icon} {provider.display_name}[/] [dim]({count_text} sessions)[/]"
                )
        else:
            self.query_one("#parent-header", Static).update(
                f"[bold]All Sessions[/] [dim]({count_text} newest first)[/]"
            )

        # Reset selection
        parent_list = self.query_one("#parent-list", ListView)
        if self.parent_sessions:
            parent_list.index = 0
        parent_list.focus()
        self.focus_pane = "parent"

    def _start_summary_generation(self):
        """Start background summary generation for sessions missing summaries."""
        if not HAS_OPENAI:
            return

        sessions_needing_summary = [
            s for s in self.parent_sessions
            if not s.is_child and s.first_prompt and not s.summary
        ]

        if not sessions_needing_summary:
            return

        for session in sessions_needing_summary:
            self._summary_queue.put(session.id)

        self._generate_summaries_background()

    @work(thread=True)
    def _generate_summaries_background(self):
        """Background worker to generate summaries using providers for message data."""
        import os
        import time
        self._summary_generating = True
        generated_count = 0
        first_error_shown = False

        if not os.environ.get("OPENAI_API_KEY"):
            self.call_from_thread(
                self.notify, "OPENAI_API_KEY not set - summaries disabled", severity="warning", timeout=3
            )
            self._summary_generating = False
            return

        while not self._summary_queue.empty():
            try:
                session_id = self._summary_queue.get_nowait()
            except Exception:
                break

            session = next((s for s in self.parent_sessions if s.id == session_id), None)
            if not session or session.summary:
                continue

            # Get full transcript from provider
            provider = get_provider(session.harness)
            messages = []
            if provider:
                try:
                    messages = provider.get_session_messages(session)
                except Exception:
                    pass
            # Fallback: build minimal transcript from DB data
            if not messages:
                db_msgs = self.db.get_session_messages(session_id)
                messages = [{"role": m.role, "content": m.content} for m in db_msgs if m.content]
            # Last resort: use first_prompt + last_response fields
            if not messages and session.first_prompt:
                messages = [{"role": "user", "content": session.first_prompt}]
                if session.last_response:
                    messages.append({"role": "assistant", "content": session.last_response})
            if not any(m.get("role") == "assistant" and m.get("content") for m in messages):
                continue

            summary = generate_summary_sync(messages)
            if summary:
                session.summary = summary
                self.db.upsert_summary(
                    session_id=session.id,
                    summary=summary,
                    model="gpt-5.2",
                    content_hash=session.content_hash or "",
                    created_at=int(time.time()),
                )
                generated_count += 1
                self.call_from_thread(self._refresh_session_item, session_id)
            elif not first_error_shown:
                first_error_shown = True
                err = getattr(generate_summary_sync, '_last_error', 'unknown')
                self.call_from_thread(
                    self.notify, f"Summary failed: {err[:120]}", severity="error", timeout=3
                )

        self._summary_generating = False

    def action_show_all_messages(self):
        """Load and display full session transcript."""
        if self.selected_session:
            self.notify("Loading transcript...")
            self._load_full_transcript(self.selected_session)

    @work(thread=True)
    def _load_full_transcript(self, session: Session):
        """Load messages from DB (fast) with provider fallback, stream to UI in batches."""
        # Try DB first (already indexed, fast SQLite lookup)
        # But verify content quality — the indexer stores empty content for Claude Code
        # sessions due to nested JSONL format mismatch. Require >50% of messages to have
        # content before trusting the DB path.
        db_msgs = self.db.get_session_messages(session.id)
        has_good_content = False
        if db_msgs:
            with_content = sum(1 for m in db_msgs if m.content)
            has_good_content = with_content > len(db_msgs) * 0.5

        if db_msgs and has_good_content:
            messages = [{"role": m.role, "content": m.content or ""} for m in db_msgs]
        else:
            # Fall back to provider (re-parses JSONL from disk)
            # This correctly handles Claude Code's nested message.content arrays
            provider = get_provider(session.harness)
            if not provider:
                return
            messages = provider.get_session_messages(session)

        detail = self.query_one("#detail-panel", SessionDetailPanel)

        # Write header on main thread
        self.call_from_thread(detail.show_full_transcript_start, session, len(messages))
        self.call_from_thread(self._focus_detail_panel)

        # Stream messages in batches for responsive UI
        BATCH_SIZE = 10
        for i, msg in enumerate(messages, 1):
            msg_text = SessionDetailPanel.build_message_text(i, msg)
            self.call_from_thread(detail.write_message, msg_text)
            # Yield to UI thread periodically
            if i % BATCH_SIZE == 0:
                import time as _time
                _time.sleep(0.01)

        # Write footer
        self.call_from_thread(detail.show_full_transcript_end)

    def _focus_detail_panel(self):
        """Focus the detail panel (must be called from main thread)."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        self.focus_pane = "detail"
        detail.focus()
        detail.scroll_home()

    def action_copy_transcript(self):
        """Copy full transcript text to clipboard (y key)."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        text = detail.get_transcript_text()
        if not text:
            self.notify("No transcript to copy", severity="warning")
            return
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            self.notify("Transcript copied to clipboard")
        except Exception:
            self.notify("Failed to copy to clipboard", severity="error")

    def action_select_all_transcript(self):
        """Select all transcript text and copy to clipboard (Ctrl+A)."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        text = detail.get_transcript_text()
        if not text:
            self.notify("No transcript to select", severity="warning")
            return
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            lines = text.count("\n") + 1
            self.notify(f"Transcript selected & copied ({lines} lines)")
        except Exception:
            self.notify("Failed to copy to clipboard", severity="error")

    def action_copy_visible_message(self):
        """Copy the nearest transcript message to clipboard (c key)."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        if not detail._transcript_messages:
            self.notify("No transcript messages", severity="warning")
            return
        # Pick the message closest to current scroll position
        # Use scroll_offset to estimate which message is visible
        scroll_y = detail.scroll_offset.y
        content_height = detail.virtual_size.height
        num_msgs = len(detail._transcript_messages)
        if num_msgs == 0:
            return
        # Estimate message index from scroll fraction
        if content_height > 0:
            fraction = scroll_y / max(1, content_height)
            idx = min(int(fraction * num_msgs), num_msgs - 1)
        else:
            idx = 0
        msg_text = detail._transcript_messages[idx].plain
        try:
            subprocess.run(["pbcopy"], input=msg_text.encode(), check=True)
            self.notify(f"Message {idx + 1}/{num_msgs} copied")
        except Exception:
            self.notify("Failed to copy to clipboard", severity="error")

    def action_reindex(self):
        """Reindex sessions and refresh the list."""
        self._run_incremental_index()

    @work(exclusive=True, thread=True)
    def _run_incremental_index(self):
        """Background worker for incremental indexing."""
        try:
            self.call_from_thread(self.notify, "Indexing sessions...")
            stats = self.indexer.incremental_update()
            if stats['sessions_indexed'] > 0:
                msg = f"Indexed {stats['sessions_indexed']} sessions"
                self.call_from_thread(self.notify, msg)
            else:
                self.call_from_thread(self.notify, "Already up to date")
            self._load_sessions()
            self.call_from_thread(self._on_sessions_loaded)
        except Exception as e:
            self.log.error(f"Indexing failed: {e}")
            self.call_from_thread(self.notify, f"Indexing failed: {e}", severity="error")

    def _refresh_session_item(self, session_id: str):
        """Refresh a specific session item in the list."""
        parent_list = self.query_one("#parent-list", ListView)
        for child in parent_list.children:
            if isinstance(child, ParentSessionItem) and child.session.id == session_id:
                child.refresh_text()
                break

    def _update_children_list(self, parent: Session):
        """Update the children list for the selected parent."""
        children_list = self.query_one("#subagent-list", ListView)
        children_list.clear()

        self.current_children = self._get_related_children(parent)

        container = self.query_one("#subagent-container")
        if self.current_children:
            container.remove_class("dimmed")
            for child in self.current_children:
                children_list.append(SubagentSessionItem(child, is_highlighted=True))
        else:
            container.add_class("dimmed")

    @on(ListView.Highlighted, "#parent-list")
    def on_parent_highlighted(self, event: ListView.Highlighted):
        """Handle parent session highlight."""
        if event.item and isinstance(event.item, ParentSessionItem):
            self.selected_session = event.item.session
            detail = self.query_one("#detail-panel", SessionDetailPanel)

            if self._search_mode:
                self._update_search_results_list(event.item.session)
                detail.show_session(event.item.session, 0)
            else:
                # Use precomputed children from cache
                self._update_children_list(event.item.session)
                child_count = len(self.current_children)
                detail.show_session(event.item.session, child_count)

    def _update_search_results_list(self, parent: Session):
        """Update bottom pane with matching children for this session."""
        results_list = self.query_one("#subagent-list", ListView)
        results_list.clear()

        # Find children that also matched the search
        related_children = self._get_related_children(parent)
        matching_children = [c for c in related_children if c.id in self._search_scores]
        self._search_matching_children = matching_children

        self.query_one("#subagent-header", Static).update(
            f"[bold yellow]Matching Sub-agents[/] [dim]({len(matching_children)})[/]"
        )

        container = self.query_one("#subagent-container")
        if matching_children:
            container.remove_class("dimmed")
            for child in matching_children:
                results_list.append(SubagentSessionItem(child, is_highlighted=True))
        else:
            container.add_class("dimmed")

    @on(ListView.Highlighted, "#subagent-list")
    def on_child_highlighted(self, event: ListView.Highlighted):
        """Handle child or search result highlight."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)

        if event.item and isinstance(event.item, SubagentSessionItem):
            self.selected_session = event.item.session
            detail.show_session(event.item.session)

    def action_switch_pane(self):
        """Switch focus between parent and child panes only (Tab)."""
        if self.focus_pane == "detail":
            return

        if self.focus_pane == "parent":
            children_list = self.query_one("#subagent-list", ListView)
            has_items = self._search_matching_children if self._search_mode else self.current_children
            if has_items:
                self.focus_pane = "subagent"
                children_list.focus()
                if children_list.index is None and len(has_items) > 0:
                    children_list.index = 0
        else:
            self.focus_pane = "parent"
            self.query_one("#parent-list", ListView).focus()

    def action_focus_detail(self):
        """Toggle between active left pane and detail panel (Shift+Tab)."""
        if self.focus_pane == "detail":
            has_items = self._search_matching_children if self._search_mode else self.current_children
            if self._last_left_pane == "subagent" and has_items:
                self.focus_pane = "subagent"
                self.query_one("#subagent-list", ListView).focus()
            else:
                self.focus_pane = "parent"
                self.query_one("#parent-list", ListView).focus()
        else:
            self._last_left_pane = self.focus_pane
            self.focus_pane = "detail"
            detail = self.query_one("#detail-panel", SessionDetailPanel)
            detail.focus()

    def action_back_to_list(self):
        """Go back to parent list (Escape)."""
        search_input = self.query_one("#search-input", Input)
        if search_input.has_focus:
            self._cancel_search()
            return

        if self._search_mode:
            self._clear_search()
            return

        if self.focus_pane == "detail":
            if self._last_left_pane == "subagent" and self.current_children:
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
        self._search_scores = {}
        self._filtered_parents = []
        self._search_matching_children = []

        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("visible")
        search_input.value = ""

        # Restore header based on current filter
        if self.active_harness_filter:
            provider = get_provider(self.active_harness_filter)
            if provider:
                self.query_one("#parent-header", Static).update(
                    f"[bold]{provider.icon} {provider.display_name}[/] [dim]({len(self.parent_sessions)} sessions)[/]"
                )
        else:
            self.query_one("#parent-header", Static).update("[bold]Sessions[/] [dim](newest first)[/]")

        self.query_one("#subagent-header", Static).update("[bold]Sub-agents[/] [dim](for selected session)[/]")

        self._populate_parent_list()

        parent_list = self.query_one("#parent-list", ListView)
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

        # Use hybrid search (FTS + semantic)
        results = self.search_engine.search(self._search_query, limit=500)
        self._search_scores = {}
        for result in results:
            self._search_scores[result.session_id] = result.score

        matching_parent_ids = set()
        for session_id in self._search_scores:
            for p in self.parent_sessions:
                if p.id == session_id:
                    matching_parent_ids.add(session_id)
                    break
            for s in self.child_sessions:
                if s.id == session_id:
                    for p in self.parent_sessions:
                        if p.project_path == s.project_path:
                            matching_parent_ids.add(p.id)

        self._filtered_parents = [p for p in self.parent_sessions if p.id in matching_parent_ids]

        search_input = self.query_one("#search-input", Input)
        search_input.remove_class("visible")

        total_matches = len(self._search_scores)
        self.query_one("#parent-header", Static).update(
            f"[bold yellow]Search:[/] [white]{query}[/] [dim]({len(self._filtered_parents)} sessions, {total_matches} matches)[/]"
        )

        parent_list = self.query_one("#parent-list", ListView)
        parent_list.clear()
        # Batch mount for performance (search results typically smaller, but be safe)
        items = [
            ParentSessionItem(session)
            for session in self._filtered_parents[:500]  # Limit display, not search
        ]
        parent_list.mount(*items)

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
            provider = get_provider(self.selected_session.harness)
            if provider:
                cmd = provider.get_resume_command(self.selected_session)
                try:
                    subprocess.run(["pbcopy"], input=cmd.encode(), check=True)
                    self.notify(f"Copied: {cmd}", title="Command Copied")
                except Exception:
                    self.notify(f"Command: {cmd}", title="Copy Failed")

    def action_resume_session(self):
        """Resume the selected session, cd-ing to its project directory first."""
        if self.selected_session:
            provider = get_provider(self.selected_session.harness)
            if provider:
                cmd = provider.get_resume_command(self.selected_session)
                project_path = str(self.selected_session.project_path)
                self.exit(result=(cmd, project_path))

    def _scroll_to_highlighted(self, lv: ListView):
        """Scroll ListView to ensure highlighted item is fully visible."""
        if lv.highlighted_child:
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
