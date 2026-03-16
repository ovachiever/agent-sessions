# Changelog

## 0.7.0 (2026-03-16)

### Features

- **Session annotations** — tag and annotate sessions with `#tag:name` and `#note text` syntax
- Claude Code `UserPromptSubmit` hook captures annotations during live sessions — pure annotation prompts are blocked from reaching the model
- Retroactive tagging from TUI via `Ctrl+T` (tag) and `Ctrl+N` (note)
- Annotations display in session detail panel with tags as `[name]` badges and timestamped notes
- `#tag:name` search modifier filters results to sessions with matching tags
- Annotation files stored at `~/.local/share/agent-sessions/annotations/` (user data, not cache)
- Schema v3 migration adds annotations table with indexes

### Improvements

- Annotation sync integrated into both full reindex and incremental update pipelines
- Hybrid search supports combined tag filters and text queries

## 0.6.1 (2026-02-18)

### Fixes

- FTS queries now require ALL terms (AND) instead of matching any (OR) — "ghostty tmux" returns sessions containing both words
- Search results sorted by relevance score instead of date
- Absolute cosine similarity threshold (0.35) filters semantic noise — keyword queries no longer return the entire database
- Score threshold (0.1) on combined results drops the weak-match tail
- Child-to-parent mapping uses `parent_id` foreign key instead of project_path heuristic
- Stop-word removal preserves compound terms like "ctrl+A" where stripping would discard meaningful parts

### Improvements

- FTS score aggregation uses MIN(rank) for best-match-per-session instead of arbitrary row
- Embedding batches capped at 250K tokens to prevent API overflow
- Internal FTS limits reduced from 100 to 50 for tighter result sets

## 0.6.0 (2026-02-12)

### Features

- Streaming transcript viewer with batched rendering for responsive UI
- Selectable text in transcripts — terminal-native highlighting and copy
- Clipboard copy: `y` for full transcript, `c` for the message at current scroll position
- DB-first transcript loading with automatic provider fallback for format-sensitive sessions
- Last response preview stored in session index for richer detail views
- FTS5 query builder with stop-word stripping
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
