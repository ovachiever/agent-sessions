"""Agent Sessions Browser TUI Application."""

import os
import subprocess
from queue import Queue
from typing import Optional

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, ListView, Static

from .cache import MetadataCache, SummaryCache, generate_summary_sync, HAS_ANTHROPIC
from .models import SearchResult, Session
from .providers import get_available_providers, get_provider
from .search import search_sessions
from .ui import (
    APP_CSS,
    ParentSessionItem,
    SearchResultItem,
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
        Binding("slash", "activate_search", "/ Search"),
        Binding("f", "cycle_filter", "Filter"),
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
        self._search_results: dict[str, list[SearchResult]] = {}
        self._filtered_parents: list[Session] = []
        self._current_session_results: list[SearchResult] = []

        # Summary generation state
        self._summary_queue: Queue = Queue()
        self._summary_generating = False

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
                yield SessionDetailPanel(id="detail-panel")
        yield Footer()

    def on_mount(self):
        """Load sessions when app mounts."""
        self.title = "Agent Sessions Browser"

        # Get available providers
        self.available_providers = get_available_providers()

        # Show loading message
        detail = self.query_one("#detail-panel", SessionDetailPanel)
        text = Text()
        text.append("Loading sessions...\n\n", style="bold cyan")
        for provider in self.available_providers:
            text.append(f"  {provider.icon} {provider.display_name}\n", style="dim")
        detail.update(text)

        # Update filter bar (will show 0 sessions initially)
        self._update_filter_bar()

        # Load sessions in background
        self._load_sessions_background()

    @work(thread=True)
    def _load_sessions_background(self):
        """Load sessions in background thread."""
        self._load_sessions()
        # Save metadata cache after loading
        MetadataCache().save()
        # Update UI on main thread
        self.call_from_thread(self._on_sessions_loaded)

    def _on_sessions_loaded(self):
        """Called when background session loading completes."""
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
        """Load sessions from all available providers."""
        self.all_sessions = []

        for provider in self.available_providers:
            sessions = provider.load_sessions()

            # Apply project filter
            if self.project_filter:
                sessions = [s for s in sessions if self.project_filter.lower() in s.project_name.lower()]

            self.all_sessions.extend(sessions)

        # Sort by modified time
        self.all_sessions.sort(key=lambda s: s.modified_time or s.created_time, reverse=True)

        # Apply harness filter and separate parents/children
        self._apply_harness_filter()

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

        # Batch mount all items at once for performance
        parent_list.clear()
        items = [ParentSessionItem(session, child_count=0) for session in sessions_to_show]
        parent_list.mount(*items)

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
        if not HAS_ANTHROPIC:
            return

        sessions_needing_summary = [
            s for s in self.parent_sessions
            if not s.is_child and s.first_prompt and s.last_response and not s.summary
        ][:50]

        if not sessions_needing_summary:
            return

        for session in sessions_needing_summary:
            self._summary_queue.put(session.id)

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

            session = next((s for s in self.parent_sessions if s.id == session_id), None)
            if not session or session.summary:
                continue

            summary = generate_summary_sync(session.first_prompt, session.last_response)
            if summary:
                session.summary = summary
                cache = SummaryCache()
                cache.set(session.id, session.content_hash, summary)
                generated_count += 1
                self.call_from_thread(self._refresh_session_item, session_id)

        if generated_count > 0:
            SummaryCache().save()

        self._summary_generating = False

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
                if self._current_session_results:
                    detail.show_search_result(self._current_session_results[0], self._search_query)
                else:
                    detail.show_session(event.item.session, 0)
            else:
                # Use precomputed children from cache
                self._update_children_list(event.item.session)
                child_count = len(self.current_children)
                detail.show_session(event.item.session, child_count)

    def _update_search_results_list(self, parent: Session):
        """Update bottom pane with search results for this session."""
        results_list = self.query_one("#subagent-list", ListView)
        results_list.clear()

        self._current_session_results = self._search_results.get(parent.id, [])

        related_children = self._get_related_children(parent)
        for child in related_children:
            if child.id in self._search_results:
                self._current_session_results.extend(self._search_results[child.id])

        self.query_one("#subagent-header", Static).update(
            f"[bold yellow]Matches[/] [dim]({len(self._current_session_results)} in this session)[/]"
        )

        container = self.query_one("#subagent-container")
        if self._current_session_results:
            container.remove_class("dimmed")
            for result in self._current_session_results:
                results_list.append(SearchResultItem(result, self._search_query))
        else:
            container.add_class("dimmed")

    @on(ListView.Highlighted, "#subagent-list")
    def on_child_highlighted(self, event: ListView.Highlighted):
        """Handle child or search result highlight."""
        detail = self.query_one("#detail-panel", SessionDetailPanel)

        if event.item and isinstance(event.item, SearchResultItem):
            self.selected_session = event.item.result.session
            detail.show_search_result(event.item.result, self._search_query)
        elif event.item and isinstance(event.item, SubagentSessionItem):
            self.selected_session = event.item.session
            detail.show_session(event.item.session)

    def action_switch_pane(self):
        """Switch focus between parent and child panes only (Tab)."""
        if self.focus_pane == "detail":
            return

        if self.focus_pane == "parent":
            children_list = self.query_one("#subagent-list", ListView)
            has_items = self._current_session_results if self._search_mode else self.current_children
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
            has_items = self._current_session_results if self._search_mode else self.current_children
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
        self._search_results = {}
        self._filtered_parents = []
        self._current_session_results = []

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

        # Search respects current harness filter
        sessions_to_search = self.parent_sessions + self.child_sessions
        self._search_results = search_sessions(sessions_to_search, self._search_query)

        matching_parent_ids = set()
        for session_id in self._search_results:
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

        total_matches = sum(len(r) for r in self._search_results.values())
        self.query_one("#parent-header", Static).update(
            f"[bold yellow]Search:[/] [white]{query}[/] [dim]({len(self._filtered_parents)} sessions, {total_matches} matches)[/]"
        )

        parent_list = self.query_one("#parent-list", ListView)
        parent_list.clear()
        # Batch mount for performance (search results typically smaller, but be safe)
        items = [
            ParentSessionItem(session, len(self._search_results.get(session.id, [])))
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
        """Resume the selected session."""
        if self.selected_session:
            provider = get_provider(self.selected_session.harness)
            if provider:
                cmd = provider.get_resume_command(self.selected_session)
                self.exit(result=cmd)

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
