"""UI widgets for Agent Sessions TUI."""

from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.widgets import ListItem, Static

from ..models import Session
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


class SessionDetailPanel(ScrollableContainer, can_focus=True):
    """Scrollable panel showing session details with selectable text."""

    EDGE_ZONE = 5  # lines from edge to trigger auto-scroll
    SCROLL_INTERVAL = 0.03  # seconds between scroll ticks
    SCROLL_BASE = 4  # minimum lines per tick

    def __init__(self, id: str = None):
        super().__init__(id=id)
        self.session: Optional[Session] = None
        self._transcript_messages: list[Text] = []
        self._dragging: bool = False
        self._scroll_direction: int = 0  # -1 up, 0 none, 1 down
        self._scroll_speed: int = 0
        self._auto_scroll_timer = None

    # -- drag-to-scroll at viewport edges --

    def on_mouse_down(self, event) -> None:
        """Track left-button press for drag-scroll."""
        if event.button == 1:
            self._stop_auto_scroll()
            self._dragging = True

    def on_mouse_up(self, event) -> None:
        """Stop drag-scrolling on button release."""
        self._dragging = False
        self._stop_auto_scroll()

    def on_mouse_move(self, event) -> None:
        """Auto-scroll when dragging near top/bottom edges."""
        if not self._dragging:
            return
        if event.y < self.EDGE_ZONE:
            # Faster the closer to the very edge (or above the widget)
            speed = self.SCROLL_BASE + max(0, self.EDGE_ZONE - event.y)
            self._start_auto_scroll(-1, speed)
        elif event.y >= self.size.height - self.EDGE_ZONE:
            speed = self.SCROLL_BASE + max(0, event.y - (self.size.height - self.EDGE_ZONE))
            self._start_auto_scroll(1, speed)
        else:
            self._stop_auto_scroll()

    def _start_auto_scroll(self, direction: int, speed: int = 4) -> None:
        if self._scroll_direction == direction and self._scroll_speed == speed:
            return
        self._stop_auto_scroll()
        self._scroll_direction = direction
        self._scroll_speed = speed
        self._auto_scroll_timer = self.set_interval(
            self.SCROLL_INTERVAL, self._do_auto_scroll
        )

    def _stop_auto_scroll(self) -> None:
        self._scroll_direction = 0
        self._scroll_speed = 0
        if self._auto_scroll_timer is not None:
            self._auto_scroll_timer.stop()
            self._auto_scroll_timer = None

    def _do_auto_scroll(self) -> None:
        if self._scroll_direction != 0:
            self.scroll_relative(
                y=self._scroll_direction * self._scroll_speed, animate=False
            )

    def update(self, text: Text) -> None:
        """Update the content (replaces all content)."""
        for child in list(self.children):
            child.remove()
        self._transcript_messages = []
        self.mount(Static(text, markup=False))

    def write(self, text: Text) -> None:
        """Append a text block."""
        self.mount(Static(text, markup=False))

    def write_message(self, text: Text) -> None:
        """Append a single message to the log (for streaming transcripts)."""
        self._transcript_messages.append(text)
        self.mount(Static(text, markup=False))

    def clear(self) -> None:
        """Clear all content."""
        for child in list(self.children):
            child.remove()
        self._transcript_messages = []

    def get_transcript_text(self) -> str:
        """Get plain text of all transcript messages for clipboard."""
        return "\n".join(t.plain for t in self._transcript_messages)

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

    def show_full_transcript_start(self, session: Session, total: int):
        """Start streaming a full transcript — write header, clear previous content."""
        self.session = session
        self.clear()
        self._transcript_messages = []

        text = Text()
        display_title = session.title or session.project_name
        text.append("━━━ Full Transcript ━━━\n", style="bold cyan")
        text.append("Session: ", style="bold")
        text.append(f"{truncate(display_title, 60)}\n")
        text.append("Path: ", style="bold")
        text.append(f"{session.project_path}\n", style="dim")
        text.append("Messages: ", style="bold")
        text.append(f"{total}\n")
        text.append("\n")

        if total == 0:
            text.append("(no messages found)\n", style="dim")

        self.write(text)

    def show_full_transcript_end(self):
        """Write the transcript footer."""
        text = Text()
        text.append("━━━ End of Transcript ━━━\n", style="bold cyan")
        text.append("Press ", style="dim")
        text.append("Shift+Tab", style="bold")
        text.append(" to return to list | ", style="dim")
        text.append("y", style="bold")
        text.append(" copy all | ", style="dim")
        text.append("c", style="bold")
        text.append(" copy visible message", style="dim")
        self.write(text)

    @staticmethod
    def build_message_text(i: int, msg: dict) -> Text:
        """Build a Rich Text object for a single transcript message."""
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

        text = Text()
        text.append(label, style=style)
        text.append("─" * max(1, 40 - len(label)), style=border_style)
        text.append("\n")

        for line in content.split("\n"):
            text.append("│ ", style=border_style)
            text.append(f"{line}\n")

        text.append("└", style=border_style)
        text.append("─" * 40, style=border_style)
        text.append("\n\n")

        return text

    def clear_display(self):
        """Clear the display."""
        self.session = None
        text = Text("Select a session to view details", style="dim")
        self.update(text)
