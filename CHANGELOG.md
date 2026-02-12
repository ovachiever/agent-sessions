# Changelog

## 0.6.0 (2026-02-12)

### Features

- Streaming transcript viewer with batched rendering for responsive UI
- Selectable text in transcripts — terminal-native highlighting and copy
- Clipboard copy: `y` for full transcript, `c` for the message at current scroll position
- DB-first transcript loading with automatic provider fallback for format-sensitive sessions
- Last response preview stored in session index for richer detail views
- FTS5 query builder with stop-word stripping and OR-joined term matching
- Schema migration (v1 → v2) with automatic FTS index rebuild

### Improvements

- Smart first-prompt and last-response extraction — skips system reminders, compaction headers, and short meta-messages
- Search results now show matching sub-agents in the bottom pane instead of inline result items
- Removed legacy per-message search result display in favor of session-level scoring

## 0.1.0 (2026-02-06)

Initial public release.

### Features

- Multi-provider session browsing: Droid, Claude Code, Cursor, OpenCode
- Split-pane TUI with parent sessions and sub-agent linking
- Full-text search across all sessions (FTS5 + optional semantic search)
- Full transcript viewer (`t` key)
- In-app incremental reindex (`i` key)
- Resume sessions directly (`r` key) with auto-cd to project directory
- AI-generated session summaries (optional, requires `openai` SDK)
- Semantic search via OpenAI embeddings (optional, requires `openai` SDK)
- Provider filtering (`f` key)
- Smart search syntax with `harness:`, `project:`, `after:`, `before:` modifiers
