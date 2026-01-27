# agent-sessions

A universal TUI for browsing and resuming sessions from multiple AI coding assistants. One tool to find any conversation you've had with any AI agent.

```
┌────────────────────────────────────────────────────────────────┐
│  agent-sessions                                                │
│                                                                │
│  "Where did I implement that auth flow?"                       │
│  "What did Cursor suggest for the caching problem?"            │
│  "Resume my Droid session on the API refactor"                 │
│                                                                │
│  → Search once, find everywhere                                │
└────────────────────────────────────────────────────────────────┘
```

## Supported Harnesses

| Harness | Status | Icon | Notes |
|---------|--------|------|-------|
| FactoryAI Droid | ✅ Supported | 🤖 | JSONL sessions in `~/.factory/sessions/` |
| Claude Code | ✅ Supported | 🧠 | JSONL sessions in `~/.claude/projects/` |
| Cursor | ✅ Supported | ⌘ | SQLite composer sessions |
| Windsurf | 📋 Planned | 🌊 | VS Code-style storage (needs data) |
| Aider | 📋 Planned | 🔧 | Project-local `.aider.chat.history.md` |
| Amp | 📋 Planned | ⚡ | Sourcegraph's agent |

## Features

- **Multi-provider support** - Browse sessions from Droid, Claude Code, and more
- **Split-pane interface** - Parent sessions on top, sub-agents on bottom
- **Provider filtering** - Press `f` to cycle through providers or show all
- **Full-text search** - Press `/` to search across all sessions
- **Smart search syntax** - `harness:droid project:api auth` filters while searching
- **Session linking** - Automatically matches sub-agents to parent sessions
- **AI summaries** - Auto-generates concise summaries via Claude Haiku
- **Quick resume** - Copy command or launch session directly

## Installation

```bash
pip install agent-sessions
```

Or install from source:

```bash
git clone https://github.com/erikjamesfritsch/agent-sessions.git
cd agent-sessions
pip install -e .
```

## Usage

### TUI Browser (default)

```bash
# Launch the TUI
agent-sessions
ais                          # short alias

# With filters
ais --harness droid          # only Droid sessions
ais --harness claude-code    # only Claude Code
ais --project api-server     # only specific project
```

### CLI Commands

```bash
# List providers
ais providers                # list available providers
ais providers --status       # detailed status with session counts

# Search from CLI
ais search "auth middleware"           # search all sessions
ais search -H droid "auth"             # search specific harness
ais search "project:api JWT"           # with inline filters

# Cache management
ais cache info               # show cache stats
ais cache clear              # clear summary cache
```

### Keybindings

| Key | Action |
|-----|--------|
| `j/k` or `↑/↓` | Navigate within focused pane |
| `Tab` | Switch between parent and sub-agent panes |
| `Shift+Tab` | Toggle to detail panel |
| `f` | Cycle harness filter (All → Droid → Claude Code → All) |
| `/` | Activate search |
| `Enter` | Copy resume command to clipboard |
| `r` | Resume selected session immediately |
| `Escape` | Cancel search / Back to list / Quit |
| `q` | Quit |

### Search Syntax

The search supports inline modifiers:

```
harness:droid          Filter by harness
project:api-server     Filter by project name
after:7d               Sessions in last 7 days
before:2024-01-15      Sessions before date
```

Examples:
```
authentication                           # simple search
harness:claude-code React component      # search Claude Code only
project:api after:1w JWT                 # API project, last week
```

## Architecture

```
agent_sessions/
├── __init__.py
├── main.py              # CLI entry point
├── app.py               # TUI application
├── models.py            # Session dataclass
├── cache.py             # AI summary cache
├── search.py            # Search engine
├── providers/
│   ├── __init__.py      # Provider registry
│   ├── base.py          # SessionProvider ABC
│   ├── droid.py         # FactoryAI Droid provider
│   ├── claude_code.py   # Claude Code provider
│   └── cursor.py        # Cursor provider
└── ui/
    ├── __init__.py
    ├── widgets.py       # TUI widgets
    └── styles.py        # CSS styles
```

## Session Locations

| Provider | Location | Format |
|----------|----------|--------|
| FactoryAI Droid | `~/.factory/sessions/` | JSONL |
| Claude Code | `~/.claude/projects/` | JSONL |
| Cursor | `~/Library/Application Support/Cursor/` | SQLite |

## Requirements

- Python 3.10+
- [Textual](https://textual.textualize.io/) >= 0.40.0
- [Rich](https://rich.readthedocs.io/) >= 13.0.0
- [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) >= 0.40.0 (optional, for AI summaries)

## Adding a New Provider

Implement the `SessionProvider` ABC:

```python
from agent_sessions.providers import register_provider
from agent_sessions.providers.base import SessionProvider

@register_provider
class MyProvider(SessionProvider):
    name = "my-tool"
    display_name = "My Tool"
    icon = "🔧"
    color = "blue"

    def get_sessions_dir(self):
        return Path.home() / ".my-tool" / "sessions"

    def discover_session_files(self):
        # Return list of session file paths
        ...

    def parse_session(self, path):
        # Parse file into Session object
        ...

    def get_resume_command(self, session):
        return f"my-tool --resume {session.id}"
```

## License

MIT
