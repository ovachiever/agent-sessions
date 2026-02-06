<p align="center">
  <h1 align="center">agent-sessions</h1>
  <p align="center">
    <strong>One TUI to browse, search, and resume every AI coding session you've ever had.</strong>
  </p>
  <p align="center">
    <a href="https://github.com/ovachiever/agent-sessions/actions"><img src="https://github.com/ovachiever/agent-sessions/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/agent-sessions/"><img src="https://img.shields.io/pypi/v/agent-sessions" alt="PyPI"></a>
    <a href="https://github.com/ovachiever/agent-sessions/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
    <img src="https://img.shields.io/pypi/pyversions/agent-sessions" alt="Python">
  </p>
</p>

---

If you use more than one AI coding assistant, your conversations are scattered across different directories, formats, and tools. **agent-sessions** unifies them into a single, fast terminal interface. Search across all your sessions at once, view full transcripts, and resume any conversation instantly -- regardless of which tool started it.

<p align="center">
  <img src="assets/screenshot.jpg?v=3" alt="agent-sessions TUI" width="900">
</p>

## Supported Providers

| Provider | Status | Sessions Location |
|----------|--------|-------------------|
| **Claude Code** | ✅ | `~/.claude/projects/` |
| **FactoryAI Droid** | ✅ | `~/.factory/sessions/` |
| **OpenCode** | ✅ | `~/.local/share/opencode/` |
| **Cursor** | ✅ | `~/Library/Application Support/Cursor/` |
| Windsurf | planned | |
| Aider | planned | |
| Amp | planned | |

## Installation

```bash
pip install agent-sessions

# or with pipx (recommended)
pipx install agent-sessions
```

From source:

```bash
git clone https://github.com/ovachiever/agent-sessions.git
cd agent-sessions
pip install -e ".[dev]"
```

## Quick Start

```bash
agent-sessions    # launch the TUI
ais               # short alias
```

On first launch, sessions are automatically discovered and indexed. Use `i` to reindex when you want to pick up new sessions without restarting.

## Keybindings

| Key | Action |
|-----|--------|
| `j/k` `↑/↓` | Navigate |
| `Tab` | Switch between session list and sub-agent list |
| `Shift+Tab` | Focus detail panel |
| `/` | Search across all sessions |
| `f` | Cycle provider filter |
| `t` | View full session transcript |
| `i` | Reindex (pick up new sessions) |
| `r` | Resume session (opens in correct project directory) |
| `Enter` | Copy resume command to clipboard |
| `q` | Quit |

## Search

Full-text search runs across every message in every session. Supports inline filters:

```
auth middleware                          # search everywhere
harness:claude-code React component      # limit to Claude Code
project:api after:7d JWT                 # project + time filter
```

Modifiers: `harness:`, `project:`, `after:` (e.g. `7d`, `1w`), `before:` (date or relative).

## Features

- **Multi-provider** -- browse Droid, Claude Code, Cursor, and OpenCode sessions in one place
- **Split-pane UI** -- parent sessions on top, linked sub-agents below
- **Full-text search** -- FTS5-indexed across all messages, with optional semantic search
- **Full transcripts** -- read any session's complete conversation
- **Session resume** -- jump back into any session with the right tool, in the right directory
- **AI summaries** -- auto-generated one-line summaries via GPT-5.2 (optional)
- **Incremental indexing** -- fast startup, only processes new/changed sessions

## Optional Dependencies

The core tool has no heavy dependencies. AI features are opt-in and require a single OpenAI API key:

```bash
pip install agent-sessions[ai]    # AI summaries + semantic search
```

### Setup

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

With the `ai` extra installed and key set, you get:

- **AI summaries** -- GPT-5.2 generates short one-line summaries for each session in the background. Summaries are cached in a local SQLite database so each session is only summarized once. You'll see lines flip from grey to white in real-time as they're generated.
- **Semantic search** -- search uses OpenAI embeddings alongside FTS5 for hybrid keyword + semantic matching, so you can find sessions by meaning, not just exact words.

## Adding a Provider

Providers are self-contained modules. Implement the `SessionProvider` ABC:

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
        ...

    def parse_session(self, path):
        ...

    def get_resume_command(self, session):
        return f"my-tool --resume {session.id}"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

## Requirements

- Python 3.10+
- [Textual](https://textual.textualize.io/) >= 0.40.0
- [Rich](https://rich.readthedocs.io/) >= 13.0.0

## License

[MIT](LICENSE)
