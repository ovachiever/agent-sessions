"""UI widgets for Agent Sessions TUI."""

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import ListItem, Static

from ..models import SearchResult, Session
from ..providers import get_provider


def truncate(text: str, max_len: int = 100) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


class ParentSessionItem(ListItem):
    """List item for parent sessions."""

    def __init__(self, session: Session, child_count: int = 0):
        super().__init__()
        self.session = session
        self.child_count = child_count
        self._static: Optional[Static] = None

    def compose(self) -> ComposeResult:
        self._static = Static(self._build_text(100))
        yield self._static

    def on_resize(self, event) -> None:
        """Update text when resized."""
        if self._static:
            self._static.update(self._build_text(self.size.width))

    def _build_text(self, width: int) -> Text:
        """Build the display text based on available width."""
        date_str = self.session.modified_time.strftime("%m-%d %H:%M") if self.session.modified_time else "??-?? ??:??"
        project = self.session.project_name

        # Get provider icon
        provider = get_provider(self.session.harness)
        icon = provider.icon if provider else "?"

        # Prefer AI summary over raw prompt
        summary = self.session.summary
        if summary:
            description = summary
            desc_style = "bold white"
        else:
            description = self.session.first_prompt or self.session.title or "(no prompt)"
            desc_style = "dim white"
        description = description.replace("\n", " ").strip()

        text = Text()
        text.append(f"{date_str}", style="cyan")
        text.append(" │ ", style="dim")
        text.append(f"{icon} ", style="bold")
        text.append(f"{project[:12]:<12}", style="green")
        text.append(" │ ", style="dim")

        # Calculate remaining width for description
        prefix_width = 36  # date(11) + sep(3) + icon(2) + project(12) + sep(3) + padding(5)
        if self.child_count > 0:
            count_str = f"({self.child_count}) "
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

    def __init__(self, session: Session, is_highlighted: bool = False):
        super().__init__()
        self.session = session
        self.is_highlighted = is_highlighted
        self._static: Optional[Static] = None

    def compose(self) -> ComposeResult:
        self._static = Static(self._build_text(100))
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

        text.append(f"{self.session.child_type:<18}", style="cyan bold")
        text.append(" │ ", style="dim")

        prefix_width = 27  # star(2) + type(18) + sep(3) + padding(4)
        desc_width = max(20, width - prefix_width)

        desc = self.session.first_prompt or "(no prompt)"
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
        self.session: Optional[Session] = None
        self._content = Static("", markup=False)

    def compose(self) -> ComposeResult:
        yield self._content

    def update(self, text: Text) -> None:
        """Update the content."""
        self._content.update(text)

    def show_session(self, session: Session, child_count: int = 0):
        """Update display with session info."""
        self.session = session
        provider = get_provider(session.harness)

        text = Text()

        text.append("━━━ Session Details ━━━\n", style="bold cyan")
        text.append("\n")

        # Harness badge
        text.append("Harness: ", style="bold")
        icon = provider.icon if provider else "?"
        display_name = provider.display_name if provider else session.harness
        text.append(f"{icon} {display_name}\n", style="cyan bold")

        if session.is_child:
            text.append("Type: ", style="bold")
            text.append("SUB-AGENT\n", style="yellow bold")
            text.append("Agent: ", style="bold")
            text.append(f"{session.child_type}\n", style="cyan bold")
        else:
            text.append("Type: ", style="bold")
            text.append("PARENT SESSION\n", style="green bold")
            if child_count > 0:
                text.append("Sub-agents: ", style="bold")
                text.append(f"{child_count}\n", style="yellow")

        # Display title
        display_title = session.title
        if session.is_child:
            display_title = session.child_type
        elif not session.title or session.title == "New Session":
            display_title = session.project_name

        text.append("Title: ", style="bold")
        text.append(f"{truncate(display_title, 50)}\n")
        text.append("Path: ", style="bold")
        text.append(f"{session.project_path}\n", style="dim")
        text.append("Date: ", style="bold")
        if session.modified_time:
            text.append(f"{session.modified_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        else:
            text.append("Unknown\n", style="dim")
        text.append("Model: ", style="bold")
        text.append(f"{session.model}\n", style="yellow")
        text.append("Session ID: ", style="bold")
        text.append(f"{session.id}\n", style="dim")
        text.append("\n")

        # Original prompt
        text.append("┌─ First Prompt ────────────────────────\n", style="bold green")
        if session.first_prompt:
            prompt_text = session.first_prompt[:2000]
            for pl in prompt_text.split("\n"):
                text.append("│ ", style="green")
                text.append(f"{pl}\n")
            if len(session.first_prompt) > 2000:
                text.append("│ ", style="green")
                text.append("... (truncated)\n", style="dim")
        else:
            text.append("│ ", style="green")
            text.append("(no prompt found)\n", style="dim")
        text.append("└───────────────────────────────────────\n", style="green")
        text.append("\n")

        # Last response
        max_resp = 1000 if session.is_child else 2000
        text.append("┌─ Last Response ────────────────────────\n", style="bold magenta")
        if session.last_response:
            resp_text = session.last_response[:max_resp]
            for rl in resp_text.split("\n"):
                text.append("│ ", style="magenta")
                text.append(f"{rl}\n")
            if len(session.last_response) > max_resp:
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
        resume_cmd = provider.get_resume_command(session) if provider else f"# Resume not available for {session.harness}"
        text.append(f" {resume_cmd} ", style="bold white on blue")
        text.append("\n\n")
        text.append("Press ", style="dim")
        text.append("Enter", style="bold")
        text.append(" to copy | ", style="dim")
        text.append("r", style="bold")
        text.append(" to resume | ", style="dim")
        text.append("Tab", style="bold")
        text.append(" switch panes", style="dim")

        self.update(text)

    def show_full_transcript(self, session: Session, messages: list[dict]):
        """Display the full session transcript with all messages."""
        self.session = session
        provider = get_provider(session.harness)

        text = Text()

        display_title = session.title or session.project_name
        text.append("━━━ Full Transcript ━━━\n", style="bold cyan")
        text.append("Session: ", style="bold")
        text.append(f"{truncate(display_title, 60)}\n")
        text.append("Path: ", style="bold")
        text.append(f"{session.project_path}\n", style="dim")
        text.append("Messages: ", style="bold")
        text.append(f"{len(messages)}\n")
        text.append("\n")

        if not messages:
            text.append("(no messages found)\n", style="dim")
            self.update(text)
            return

        for i, msg in enumerate(messages, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user":
                label = f"┌─ [{i}] User "
                style = "bold green"
                border_style = "green"
            else:
                label = f"┌─ [{i}] Assistant "
                style = "bold magenta"
                border_style = "magenta"

            text.append(label, style=style)
            text.append("─" * max(1, 40 - len(label)), style=border_style)
            text.append("\n")

            for line in content.split("\n"):
                text.append("│ ", style=border_style)
                text.append(f"{line}\n")

            text.append("└", style=border_style)
            text.append("─" * 40, style=border_style)
            text.append("\n\n")

        text.append("━━━ End of Transcript ━━━\n", style="bold cyan")
        text.append("Press ", style="dim")
        text.append("Shift+Tab", style="bold")
        text.append(" to return to list", style="dim")

        self.update(text)

    def show_search_result(self, result: SearchResult, query: str):
        """Show a search result with highlighted context."""
        self.session = result.session
        provider = get_provider(result.session.harness)

        text = Text()

        text.append("━━━ Search Result ━━━\n", style="bold yellow")
        text.append("\n")

        # Display title
        display_title = result.session.title
        if not display_title or display_title == "New Session":
            display_title = result.session.project_name

        text.append("Session: ", style="bold")
        text.append(f"{truncate(display_title, 50)}\n")
        text.append("Path: ", style="bold")
        text.append(f"{result.session.project_path}\n", style="dim")
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
        resume_cmd = provider.get_resume_command(result.session) if provider else f"# Resume not available"
        text.append(f" {resume_cmd} ", style="bold white on blue")
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
