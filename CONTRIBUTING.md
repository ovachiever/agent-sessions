# Contributing

## Development Setup

```bash
git clone https://github.com/ovachiever/agent-sessions.git
cd agent-sessions
pip install -e ".[dev]"
pytest
```

Optional AI features require:

```bash
pip install -e ".[ai,dev]"
export OPENAI_API_KEY="sk-..."
```

## Repository Layout

| Path | Purpose |
| --- | --- |
| `agent_sessions/` | Runtime package |
| `agent_sessions/providers/` | Provider implementations |
| `agent_sessions/index/` | SQLite schema, indexing, chunking, embeddings, hybrid search |
| `agent_sessions/ui/` | Textual widgets and styles |
| `tests/` | Unit and Textual pilot tests |
| `assets/` | Public README assets |
| `.github/workflows/` | CI |
| `.dev/` | Ignored local notes, generated metadata, caches, and other non-release artifacts |

Do not commit generated build outputs, local editor files, provider credentials, `.env`, SQLite databases, `.pytest_cache/`, `*.egg-info/`, or `.dev/`.

## Test Commands

```bash
pytest
```

CI runs the test suite on Python 3.10, 3.11, and 3.12.

## Search Architecture

The active search path is `HybridSearch` in `agent_sessions/index/search.py`.

Search behavior:

- Parses natural-language phrasing into a focused search topic.
- Supports `harness:`, `project:`, `after:`, `before:`, and `#tag:` filters.
- Runs FTS5 over indexed messages and session metadata.
- Runs semantic search over embedded chunks when OpenAI embeddings are available.
- Combines scores at the session level.
- Carries match snippets and match source labels to CLI and TUI display.
- Propagates child/sub-agent matches up to parent session results.

When changing search behavior, add focused tests in `tests/test_hybrid_search.py` and run the full suite.

## Adding a Provider

1. Add a module under `agent_sessions/providers/`.
2. Implement `SessionProvider`.
3. Register the provider with `@register_provider`.
4. Return normalized `Session` objects from `parse_session`.
5. Return ordered user/assistant messages from `get_session_messages`.
6. Add fixture-based provider tests.

Provider skeleton:

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

Provider parsers should filter system/meta messages from transcripts and keep resume commands faithful to the upstream tool.

## Annotations

Annotations are stored as JSON files under:

```text
~/.local/share/agent-sessions/annotations/{session_id}.json
```

They sync into the SQLite index during full and incremental indexing. Tests for annotation persistence and tag filtering should use temporary database instances, not real user data.

## Release Checklist

Before publishing:

1. Update `pyproject.toml` and `agent_sessions/__init__.py` to the same version.
2. Update `CHANGELOG.md` with user-facing changes.
3. Keep README and CONTRIBUTING aligned with implemented behavior.
4. Run `pytest`.
5. Run `git diff --check`.
6. Ensure `git status --short` contains only intentional tracked changes.
