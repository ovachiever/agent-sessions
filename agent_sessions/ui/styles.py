"""CSS styles for Agent Sessions TUI."""

APP_CSS = """
Screen {
    layout: horizontal;
}

#left-container {
    width: 55%;
    height: 100%;
}

#parent-container {
    height: 60%;
    border: solid $primary;
}

#subagent-container {
    height: 40%;
    border: solid $warning;
}

#detail-container {
    width: 45%;
    height: 100%;
    border: solid $secondary;
    padding: 1;
}

#parent-list, #subagent-list {
    height: 1fr;
}

.list-header {
    height: auto;
    background: $surface;
    padding: 0 1;
    text-style: bold;
}

#parent-header {
    color: $primary;
}

#subagent-header {
    color: $warning;
}

#search-input {
    display: none;
    height: 3;
    border: solid $warning;
    padding: 0 1;
}

#search-input.visible {
    display: block;
}

#detail-panel {
    height: 100%;
    overflow-y: auto;
    scrollbar-gutter: stable;
}

#detail-panel:focus {
    border: solid $success;
}

ParentSessionItem, SubagentSessionItem {
    height: 1;
    padding: 0 1;
}

ParentSessionItem:hover, SubagentSessionItem:hover {
    background: $surface-lighten-1;
}

ListView:focus > ListItem.-active {
    background: $primary-darken-1;
}

ListView.-has-focus > ListItem.-active {
    background: $primary;
}

#subagent-container.dimmed {
    opacity: 0.5;
}

Footer {
    background: $surface;
}
"""
