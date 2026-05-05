# agent-sessions

Universal terminal UI for browsing, searching, reading, annotating, and resuming AI coding sessions across multiple assistant tools.

`agent-sessions` indexes local conversation stores from supported coding assistants into one SQLite database, then exposes them through a fast Textual TUI and a small CLI.

![agent-sessions TUI](assets/screenshot.jpg?v=3)

## Supported Providers

| Provider | Status | Session location |
| --- | --- | --- |
| Claude Code | Supported | `~/.claude/projects/` |
| Codex | Supported | `~/.codex/sessions/` |
| FactoryAI Droid | Supported | `~/.factory/sessions/` |
| Cursor | Supported | `~/Library/Application Support/Cursor/` |
| OpenCode | Supported | `~/.local/share/opencode/` |

## Install

```bash
pip install agent-sessions
```

For optional AI summaries and semantic search:

```bash
pip install "agent-sessions[ai]"
export OPENAI_API_KEY="sk-..."
```

From source:

```bash
git clone https://github.com/ovachiever/agent-sessions.git
cd agent-sessions
pip install -e ".[dev]"
pytest
```

## Use

```bash
agent-sessions
ais
```

The first launch indexes available provider stores. Press `i` in the TUI to reindex later.

Common CLI commands:

```bash
agent-sessions providers --status
agent-sessions search "find me the sessions where we worked on auth token refresh"
agent-sessions search "harness:codex project:api after:7d natural language search"
agent-sessions --stats
agent-sessions --reindex
agent-sessions --generate-embeddings
```

## Keybindings

| Key | Action |
| --- | --- |
| `/` | Search sessions, or find within an open transcript |
| `s` | Cycle search sort order: relevance, newest, oldest |
| `f` | Cycle provider filter |
| `t` | Load full transcript |
| `Ctrl+T` | Add tag to selected session |
| `Ctrl+N` | Add note to selected session |
| `y` | Copy full transcript |
| `c` | Copy visible transcript message |
| `r` | Resume selected session |
| `Enter` | Copy resume command |
| `i` | Reindex sessions |
| `Tab` | Switch parent/sub-agent panes |
| `Shift+Tab` | Focus detail panel |
| `Escape` | Back, clear search, or close find |
| `q` | Quit |

## Search

Search is session-level and hybrid:

- FTS5 keyword search over indexed messages and session metadata.
- Optional semantic search over embedded transcript chunks when `agent-sessions[ai]` and `OPENAI_API_KEY` are available.
- Natural-language query cleanup for prompts like `find me the sessions where we worked on X`.
- Inline filters for provider, project, time range, and annotation tags.
- Match explanations in CLI output and the TUI detail panel.
- Child/sub-agent matches propagate to the parent session result.

Examples:

```bash
auth middleware
find me the sessions where we worked on webhook replay handling
harness:claude-code React component
project:api after:7d JWT refresh
before:2026-04-01 indexing
#tag:breakthrough semantic search
```

Supported filters:

| Filter | Meaning |
| --- | --- |
| `harness:name` | Provider name, such as `codex`, `claude-code`, `droid`, `cursor`, or `opencode` |
| `project:name` | Project name or path substring |
| `after:value` | Session modified after ISO date or relative value like `7d`, `2w`, `24h` |
| `before:value` | Session modified before ISO date or relative value |
| `#tag:name` | Sessions with a matching annotation tag |

## Transcripts

Press `t` on a session to stream the full transcript into a selectable text view. Transcript search uses `/`, `n`, and `Shift+N` inside the transcript view. The indexed transcript loader falls back to provider-native parsing when a provider format needs special handling.

## Annotations

Annotations are stored outside cache at:

```text
~/.local/share/agent-sessions/annotations/{session_id}.json
```

In the TUI:

- `Ctrl+T` adds a tag.
- `Ctrl+N` adds a note.

During Claude Code sessions, an optional local `UserPromptSubmit` hook can capture `#tag:name` and `#note text` markup. Pure annotation prompts can be blocked from reaching the model; mixed prompts pass through with annotation markup stripped.

Tags and notes sync into the SQLite index during reindex and are shown in the session detail panel.

## AI Features

AI features are opt-in and require `agent-sessions[ai]` plus `OPENAI_API_KEY`.

- Summaries use `gpt-5.2` to generate concise session titles.
- Semantic search uses `text-embedding-3-small` embeddings over session chunks.
- Embeddings are cached in SQLite. Use `agent-sessions --generate-embeddings` to backfill chunks that were indexed before AI features were enabled.

The core TUI, provider parsing, transcript browsing, annotations, and keyword search work without AI dependencies.

## Storage

| Data | Location |
| --- | --- |
| SQLite index | `~/.cache/agent-sessions/sessions.db` |
| Legacy summary cache | `~/.cache/agent-sessions/summaries.json` |
| Metadata cache | `~/.cache/agent-sessions/metadata.json` |
| Annotation files | `~/.local/share/agent-sessions/annotations/` |

The index is disposable. Source sessions remain in each provider's own store.

## Provider Contract

Providers implement `SessionProvider` and return normalized `Session` objects plus user/assistant messages:

```python
from pathlib import Path

from agent_sessions.providers import register_provider
from agent_sessions.providers.base import SessionProvider


@register_provider
class MyProvider(SessionProvider):
    name = "my-tool"
    display_name = "My Tool"
    icon = "T"
    color = "blue"

    def get_sessions_dir(self) -> Path:
        return Path.home() / ".my-tool" / "sessions"

    def discover_session_files(self) -> list[Path]:
        ...

    def parse_session(self, path: Path):
        ...

    def get_session_messages(self, session) -> list[dict]:
        ...

    def get_resume_command(self, session) -> str:
        return f"my-tool --resume {session.id}"
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

Release-facing files are kept at the repository root. Local notes, generated metadata, caches, and other non-release artifacts belong in ignored `.dev/`.

## License

MIT. See [LICENSE](LICENSE).
