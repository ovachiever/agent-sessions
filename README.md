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

If you use more than one AI coding assistant, your conversations are scattered across different directories, formats, and tools. **agent-sessions** pulls them into a single terminal interface. Search across every session at once, read full transcripts with selectable text, copy what you need, and resume any conversation — regardless of which tool started it.

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

On first launch, sessions are discovered and indexed automatically. Press `i` at any time to pick up new sessions without restarting.

## Keybindings

| Key | Action |
|-----|--------|
| `j/k` `↑/↓` | Navigate session list |
| `Tab` | Switch between session list and sub-agent list |
| `Shift+Tab` | Focus detail panel (for scrolling and text selection) |
| `/` | Search across all sessions |
| `f` | Cycle provider filter |
| `t` | Load full session transcript |
| `Ctrl+T` | Add a tag to selected session |
| `Ctrl+N` | Add a note to selected session |
| `y` | Copy entire transcript to clipboard |
| `c` | Copy visible message to clipboard |
| `r` | Resume session (opens in correct project directory) |
| `Enter` | Copy resume command to clipboard |
| `i` | Reindex (pick up new sessions) |
| `Escape` | Back / clear search |
| `q` | Quit |

## Search

Full-text search runs across every message in every session, with optional semantic matching when AI features are enabled. Supports inline filters:

```
auth middleware                          # search everywhere
harness:claude-code React component      # limit to Claude Code
project:api after:7d JWT                 # project + time filter
#tag:breakthrough                        # filter by annotation tag
#tag:bugfix authentication               # combine tag filter with text search
```

Modifiers: `harness:`, `project:`, `after:` (e.g. `7d`, `1w`), `before:` (date or relative), `#tag:` (annotation tag).

## Annotations

Tag and annotate sessions to mark important moments, breakthroughs, or decisions.

### During a Claude Code session

Use `#tag:` and `#note` markup in your prompts. A `UserPromptSubmit` hook captures annotations and stores them to disk.

```
#tag:breakthrough                                    # pure tag — blocked from model
#tag:bugfix #note auth token was expired             # pure annotations — blocked
fix the parser #tag:refactor                         # mixed — model sees "fix the parser", tag saved
```

Pure annotation prompts (no other content) are blocked from reaching the model. Mixed prompts pass through with the annotation stripped for the model's context.

### From the TUI

Press `Ctrl+T` to add a tag or `Ctrl+N` to add a note to the currently selected session. Annotations appear in the session detail panel and are searchable.

### Setup

The hook is installed at `~/.claude/hooks/annotate.py` and registered in `~/.claude/settings.json`. Annotation files are stored at `~/.local/share/agent-sessions/annotations/`. They sync into the database automatically during indexing.

## Features

- **Multi-provider** — browse Droid, Claude Code, Cursor, and OpenCode sessions in one place
- **Split-pane UI** — parent sessions on top, linked sub-agents below
- **Full-text search** — FTS5-indexed across all messages, with optional hybrid semantic search
- **Session annotations** — tag and annotate sessions inline or retroactively; searchable via `#tag:` syntax
- **Streaming transcripts** — read any session's complete conversation, streamed in batches for responsiveness
- **Selectable text** — highlight and copy directly from transcript content using your terminal's native selection
- **Clipboard integration** — `y` copies the full transcript, `c` copies the message at your scroll position
- **Session resume** — jump back into any session with the right tool, in the right directory
- **AI summaries** — auto-generated one-line summaries via GPT-5.2 (optional)
- **Incremental indexing** — fast startup; only processes new or changed sessions

## Transcript Viewer

Press `t` on any session to load its full conversation. Messages stream into the detail panel as they load — user messages bordered in green, assistant messages in magenta, each numbered for reference.

The transcript renders as selectable terminal text. Highlight with your mouse to copy specific passages, or use `y` to copy everything at once. Press `c` to copy the message nearest your scroll position.

For sessions with nested JSONL formats (like Claude Code), the viewer automatically falls back to the provider's native parser when the indexed content is incomplete.

## Optional Dependencies

The core tool has no heavy dependencies. AI features are opt-in:

```bash
pip install agent-sessions[ai]    # AI summaries + semantic search
```

### Setup

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

With the `ai` extra installed and key set, you get:

- **AI summaries** — GPT-5.2 generates short one-line summaries for each session in the background. Summaries are cached in a local SQLite database, so each session is only summarized once. Lines flip from grey to white in real-time as they arrive.
- **Semantic search** — OpenAI embeddings run alongside FTS5 for hybrid keyword + semantic matching. Find sessions by meaning, not just exact words.

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

    def get_session_messages(self, session):
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
