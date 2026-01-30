# SQLite Session Index V2 - With Semantic Search

## TL;DR

> **Quick Summary**: Implement SQLite-based session index with FTS5 keyword search and semantic vector search for natural language queries like "find the session where we used agent-do excel for ninety.io automation"
> 
> **Deliverables**:
> - SQLite database with sessions, messages, chunks tables
> - FTS5 full-text search
> - Semantic search with OpenAI embeddings
> - Hybrid search combining both
> - Auto-tagging system
> - Incremental indexing
> 
> **Estimated Effort**: Large (multi-phase)
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Schema → Indexer → FTS → Semantic

---

## Context

### Original Request
Update the SQLITE_INDEX_PLAN.md with comprehensive review, adding semantic search support, auto-tagging, and additional metrics for future expansion.

### Key Decisions Made
1. **Token tracking**: Not needed
2. **Code language detection**: Not needed  
3. **Chunking strategy**: Recursive chunking at ~400 tokens respecting conversation turn boundaries, with dedicated tool-usage chunks
4. **Search history**: Store semantic searches only, keep indefinitely
5. **Tags**: Auto-generate from content patterns

### Research Findings
- **sqlite-vector** recommended over abandoned sqlite-vss
- Hybrid search (FTS + semantic) critical - pure vector fails on exact matches
- Turn-based chunking with 400-512 tokens optimal for retrieval
- Session-summary chunks needed for broad topic matching

---

## Work Objectives

### Core Objective
Replace file-scanning with SQLite database supporting both keyword (FTS5) and semantic (vector) search for finding sessions by natural language queries.

### Concrete Deliverables
- `agent_sessions/index/database.py` - SessionDatabase class
- `agent_sessions/index/indexer.py` - Full/incremental indexing
- `agent_sessions/index/chunker.py` - Turn-based chunking for semantic search
- `agent_sessions/index/tagger.py` - Auto-tag generation
- `agent_sessions/index/embeddings.py` - OpenAI embedding integration
- `agent_sessions/index/search.py` - Hybrid search implementation
- Updated `docs/SQLITE_INDEX_PLAN.md` with final schema

### Definition of Done
- [ ] `agent-sessions --reindex` builds full index
- [ ] `agent-sessions --search "keyword"` returns FTS results <100ms
- [ ] `agent-sessions --search "agent-do excel ninety.io automation"` finds correct session via semantic search
- [ ] Incremental update completes <500ms on typical startup
- [ ] Auto-tags visible in session list

### Must Have
- SQLite database at `~/.cache/agent-sessions/sessions.db`
- FTS5 for keyword search
- Semantic search with graceful degradation (works without OpenAI key)
- Incremental indexing (mtime-based change detection)
- Auto-generated tags from content

### Must NOT Have (Guardrails)
- No breaking changes to existing Session model
- No required API keys (semantic search optional)
- No blocking UI during indexing (background worker)
- No deletion of original session files
- No external database dependencies (SQLite only)

---

## Database Schema

### Core Tables

```sql
-- Schema versioning
CREATE TABLE schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER DEFAULT (strftime('%s', 'now')),
    description TEXT
);

-- Index metadata
CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Sessions
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    harness TEXT NOT NULL,
    project_path TEXT,
    project_name TEXT,
    timestamp INTEGER NOT NULL,
    timestamp_end INTEGER,
    is_child BOOLEAN DEFAULT FALSE,
    parent_id TEXT,
    child_type TEXT,
    message_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    first_prompt_preview TEXT,
    file_path TEXT,
    file_mtime INTEGER,
    indexed_at INTEGER,
    auto_tags TEXT,  -- JSON array
    FOREIGN KEY (parent_id) REFERENCES sessions(id)
);

-- Messages
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp INTEGER,
    sequence INTEGER,
    has_code BOOLEAN DEFAULT FALSE,
    tool_mentions TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Chunks for semantic search
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_id TEXT,
    chunk_index INTEGER NOT NULL,
    chunk_type TEXT DEFAULT 'turn',  -- 'turn', 'summary', 'tool_usage'
    content TEXT NOT NULL,
    metadata TEXT,
    embedding BLOB,
    embedding_model TEXT,
    created_at INTEGER DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Semantic search history
CREATE TABLE semantic_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    result_count INTEGER,
    top_session_ids TEXT,
    search_time_ms INTEGER,
    timestamp INTEGER DEFAULT (strftime('%s', 'now'))
);

-- Project stats
CREATE TABLE project_stats (
    project_path TEXT PRIMARY KEY,
    project_name TEXT,
    total_sessions INTEGER DEFAULT 0,
    parent_sessions INTEGER DEFAULT 0,
    child_sessions INTEGER DEFAULT 0,
    first_session_time INTEGER,
    last_session_time INTEGER,
    harness_counts TEXT,
    total_messages INTEGER DEFAULT 0,
    common_tags TEXT,
    updated_at INTEGER
);

-- AI summaries (migrated from JSON cache)
CREATE TABLE summaries (
    session_id TEXT PRIMARY KEY,
    summary TEXT,
    model TEXT,
    content_hash TEXT,
    created_at INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

### FTS5 Tables

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 1'
);

CREATE VIRTUAL TABLE sessions_fts USING fts5(
    first_prompt_preview,
    project_name,
    auto_tags,
    content='sessions',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
```

---

## Verification Strategy

### Test Infrastructure
- **Infrastructure exists**: YES (pytest in tests/)
- **User wants tests**: YES (Tests-after)
- **Framework**: pytest

### Manual Verification Required
For each phase, verify:
1. CLI commands work as expected
2. Performance meets targets
3. No regressions in existing functionality

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Start Immediately):
├── Task 1: Create database.py with schema
├── Task 2: Create tagger.py with pattern matching
└── Task 3: Create chunker.py with turn-based chunking

Wave 2 (After Wave 1):
├── Task 4: Create indexer.py (depends: 1, 2, 3)
├── Task 5: Create embeddings.py (depends: 3)
└── Task 6: Update providers with discover_sessions_fast()

Wave 3 (After Wave 2):
├── Task 7: Create hybrid search (depends: 4, 5)
├── Task 8: Integrate with app.py (depends: 4, 7)
└── Task 9: Add CLI commands (depends: 4, 7, 8)

Wave 4 (Final):
└── Task 10: Update docs/SQLITE_INDEX_PLAN.md (depends: all)
```

### Dependency Matrix

| Task | Depends On | Blocks | Parallel With |
|------|------------|--------|---------------|
| 1 | None | 4, 7 | 2, 3 |
| 2 | None | 4 | 1, 3 |
| 3 | None | 4, 5 | 1, 2 |
| 4 | 1, 2, 3 | 7, 8, 9 | 5, 6 |
| 5 | 3 | 7 | 4, 6 |
| 6 | None | 8 | 4, 5 |
| 7 | 4, 5 | 8, 9 | None |
| 8 | 4, 7 | 9 | None |
| 9 | 4, 7, 8 | 10 | None |
| 10 | All | None | None |

---

## TODOs

- [ ] 1. Create database.py with SQLite schema

  **What to do**:
  - Create `agent_sessions/index/__init__.py`
  - Create `agent_sessions/index/database.py` with SessionDatabase class
  - Implement schema creation with all tables
  - Implement FTS5 tables and triggers
  - Add schema versioning and migration support
  - Implement basic CRUD: upsert_session, upsert_messages, delete_session
  - Implement query methods: get_sessions, get_session, get_parents, get_children

  **Must NOT do**:
  - No embedding-related queries yet (Task 7)
  - No search methods yet (Task 7)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`cloudflare-d1`, `sql-optimization-patterns`]
    - `cloudflare-d1`: SQLite patterns and FTS5 expertise
    - `sql-optimization-patterns`: Index design and query optimization

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 3)
  - **Blocks**: Tasks 4, 7
  - **Blocked By**: None

  **References**:
  - `agent_sessions/cache.py:22-76` - Existing MetadataCache pattern for singleton + file I/O
  - `agent_sessions/models.py:10-47` - Session dataclass fields to map to schema
  - Current plan schema in this document

  **Acceptance Criteria**:
  - [ ] `SessionDatabase()` creates DB at `~/.cache/agent-sessions/sessions.db`
  - [ ] All tables created with correct schema
  - [ ] FTS5 triggers fire on insert/update/delete
  - [ ] `db.upsert_session(session)` stores and retrieves correctly
  - [ ] `db.get_parents("opencode")` returns only parent sessions for harness

  **Commit**: YES
  - Message: `feat(index): add SessionDatabase with SQLite schema and FTS5`
  - Files: `agent_sessions/index/__init__.py`, `agent_sessions/index/database.py`

---

- [ ] 2. Create tagger.py with auto-tag generation

  **What to do**:
  - Create `agent_sessions/index/tagger.py`
  - Implement AutoTagger class with pattern matching
  - Extract tool mentions (agent-do, git, npm, etc.)
  - Extract activity types (debugging, implementing, refactoring)
  - Extract technology mentions (react, python, prisma, etc.)
  - Add project name as tag
  - Limit to 15 most relevant tags

  **Must NOT do**:
  - No AI-based tagging (keep it fast, pattern-based)
  - No external API calls

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 3)
  - **Blocks**: Task 4
  - **Blocked By**: None

  **References**:
  - `agent_sessions/search.py:14-32` - extract_text_content pattern for content processing
  - `agent_sessions/providers/opencode.py:42-58` - _detect_child_type pattern matching example

  **Acceptance Criteria**:
  - [ ] `tagger.generate_tags(session, messages)` returns list of strings
  - [ ] Tags include tool mentions like `tool:agent-do-excel`
  - [ ] Tags include activities like `debugging`, `implementing`
  - [ ] Tags include tech like `python`, `react`
  - [ ] Max 15 tags returned

  **Commit**: YES
  - Message: `feat(index): add AutoTagger for pattern-based tag generation`
  - Files: `agent_sessions/index/tagger.py`

---

- [ ] 3. Create chunker.py with turn-based chunking

  **What to do**:
  - Create `agent_sessions/index/chunker.py`
  - Implement SessionChunker class
  - Create session summary chunk (project, tags, first prompt, tools)
  - Create turn-based chunks (~400 tokens, respecting message boundaries)
  - Create dedicated tool-usage chunks for agent-do mentions
  - Add token estimation (chars/4 approximation)

  **Must NOT do**:
  - No embedding generation (Task 5)
  - No actual API calls

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: [`rag-implementation`]
    - `rag-implementation`: Chunking strategies for retrieval

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:
  - `agent_sessions/models.py:10-47` - Session model structure
  - Chunking strategy in this plan document

  **Acceptance Criteria**:
  - [ ] `chunker.chunk_session(session, messages)` returns list of Chunk objects
  - [ ] First chunk is always type='summary' with session context
  - [ ] Turn chunks are ~400 tokens max
  - [ ] Tool usage creates dedicated chunks with metadata
  - [ ] Chunk dataclass has: session_id, chunk_type, content, metadata, embedding(None)

  **Commit**: YES
  - Message: `feat(index): add SessionChunker with turn-based chunking strategy`
  - Files: `agent_sessions/index/chunker.py`

---

- [ ] 4. Create indexer.py with full/incremental indexing

  **What to do**:
  - Create `agent_sessions/index/indexer.py`
  - Implement SessionIndexer class
  - Implement `full_reindex()` with progress callback
  - Implement `incremental_update()` with mtime-based change detection
  - Special handling for OpenCode (per-message files)
  - Integrate tagger and chunker
  - Refresh project_stats after indexing

  **Must NOT do**:
  - No embedding generation (handled separately)
  - No UI integration (Task 8)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`async-python-patterns`]
    - `async-python-patterns`: Efficient async processing patterns

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 5, 6)
  - **Blocks**: Tasks 7, 8, 9
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `agent_sessions/providers/base.py:41-51` - load_sessions pattern
  - `agent_sessions/providers/opencode.py:73-86` - discover_session_files for OpenCode
  - `agent_sessions/cache.py:61-75` - mtime-based cache invalidation pattern
  - Incremental update algorithm in this plan

  **Acceptance Criteria**:
  - [ ] `indexer.full_reindex()` indexes all sessions from all providers
  - [ ] `indexer.incremental_update()` only processes new/changed sessions
  - [ ] OpenCode sessions with new messages are detected and reindexed
  - [ ] Auto-tags are generated and stored
  - [ ] Chunks are generated and stored (without embeddings)
  - [ ] project_stats table is populated

  **Commit**: YES
  - Message: `feat(index): add SessionIndexer with full and incremental indexing`
  - Files: `agent_sessions/index/indexer.py`

---

- [ ] 5. Create embeddings.py with OpenAI integration

  **What to do**:
  - Create `agent_sessions/index/embeddings.py`
  - Implement EmbeddingGenerator class
  - Use OpenAI text-embedding-3-small (1536 dims)
  - Implement batch embedding (100 at a time)
  - Implement query embedding for search
  - Serialize embeddings as BLOB (struct.pack float32)
  - Graceful degradation when no API key

  **Must NOT do**:
  - No blocking on missing API key
  - No error propagation (log and continue)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: [`openai-api`]
    - `openai-api`: OpenAI embeddings API usage

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 6)
  - **Blocks**: Task 7
  - **Blocked By**: Task 3

  **References**:
  - `agent_sessions/cache.py:136-169` - Anthropic API pattern with graceful fallback
  - OpenAI embeddings API documentation

  **Acceptance Criteria**:
  - [ ] `embedder.embed_chunks(chunks)` returns chunks with embedding BLOB
  - [ ] `embedder.embed_query("search text")` returns list[float] or None
  - [ ] Works without OPENAI_API_KEY (returns None, no errors)
  - [ ] Batch processes in groups of 100
  - [ ] Embeddings serialize/deserialize correctly

  **Commit**: YES
  - Message: `feat(index): add EmbeddingGenerator with OpenAI integration`
  - Files: `agent_sessions/index/embeddings.py`

---

- [ ] 6. Update providers with discover_sessions_fast()

  **What to do**:
  - Add `discover_sessions_fast() -> dict[str, int]` to base.py
  - Implement in each provider (returns {session_id: mtime})
  - Add `get_session_messages(session) -> list[Message]` to base.py
  - Implement in each provider

  **Must NOT do**:
  - No breaking changes to existing methods
  - No full parsing in discover_sessions_fast (just IDs and mtimes)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 4, 5)
  - **Blocks**: Task 8
  - **Blocked By**: None

  **References**:
  - `agent_sessions/providers/base.py` - SessionProvider ABC
  - `agent_sessions/providers/opencode.py:73-86` - discover_session_files
  - `agent_sessions/providers/claude_code.py` - Claude Code provider
  - `agent_sessions/providers/droid.py` - Droid provider

  **Acceptance Criteria**:
  - [ ] `provider.discover_sessions_fast()` returns {id: mtime} dict
  - [ ] Fast - no full parsing, just file stats
  - [ ] `provider.get_session_messages(session)` returns Message objects
  - [ ] All three providers (opencode, claude_code, droid) updated

  **Commit**: YES
  - Message: `feat(providers): add discover_sessions_fast and get_session_messages`
  - Files: `agent_sessions/providers/base.py`, `agent_sessions/providers/opencode.py`, `agent_sessions/providers/claude_code.py`, `agent_sessions/providers/droid.py`

---

- [ ] 7. Create hybrid search implementation

  **What to do**:
  - Create `agent_sessions/index/search.py` (new file, different from existing search.py)
  - Implement HybridSearch class
  - Add `search_messages_fts()` to database.py
  - Add `search_semantic()` to database.py using cosine similarity
  - Implement score normalization and combination
  - Add `log_semantic_search()` for search history
  - Default weights: FTS 0.3, Semantic 0.7

  **Must NOT do**:
  - No sqlite-vector dependency yet (use pure Python cosine similarity)
  - No blocking if embeddings unavailable

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: [`rag-implementation`, `sql-optimization-patterns`]
    - `rag-implementation`: Hybrid search patterns
    - `sql-optimization-patterns`: Efficient FTS5 queries

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential)
  - **Blocks**: Tasks 8, 9
  - **Blocked By**: Tasks 4, 5

  **References**:
  - `agent_sessions/search.py` - Existing search implementation (FTS patterns)
  - `agent_sessions/index/database.py` - Database class to extend
  - Hybrid search algorithm in this plan

  **Acceptance Criteria**:
  - [ ] `search.search("query")` returns ranked session list
  - [ ] FTS-only works when no embeddings available
  - [ ] Hybrid combines FTS and semantic scores correctly
  - [ ] Search history logged to semantic_searches table
  - [ ] Query "agent-do excel ninety.io" finds relevant sessions

  **Commit**: YES
  - Message: `feat(index): add HybridSearch combining FTS5 and semantic search`
  - Files: `agent_sessions/index/search.py`, `agent_sessions/index/database.py`

---

- [ ] 8. Integrate with app.py

  **What to do**:
  - Modify `app.py` to use SessionDatabase for loading
  - Run incremental_update() in background worker on mount
  - Add progress indicator during indexing
  - Integrate HybridSearch for search functionality
  - Display auto-tags in session list

  **Must NOT do**:
  - No blocking UI during indexing
  - No removal of existing fallback behavior

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential after Task 7)
  - **Blocks**: Task 9
  - **Blocked By**: Tasks 4, 6, 7

  **References**:
  - `agent_sessions/app.py` - Main TUI application
  - `agent_sessions/ui/widgets.py` - UI widgets to update

  **Acceptance Criteria**:
  - [ ] App loads sessions from database instead of file scanning
  - [ ] "Indexing..." status shown during incremental update
  - [ ] Search uses hybrid search when available
  - [ ] Auto-tags visible in session details
  - [ ] No UI freezing during indexing

  **Commit**: YES
  - Message: `feat(app): integrate SQLite index for fast loading and search`
  - Files: `agent_sessions/app.py`, `agent_sessions/ui/widgets.py`

---

- [ ] 9. Add CLI commands

  **What to do**:
  - Add `--reindex` flag for full reindex
  - Add `--generate-embeddings` for batch embedding generation
  - Add `--stats` for database statistics
  - Add `--projects` for project activity listing
  - Add `--search-history` for search pattern analysis
  - Update `--search` to use hybrid search

  **Must NOT do**:
  - No breaking changes to existing CLI

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (sequential after Task 8)
  - **Blocks**: Task 10
  - **Blocked By**: Tasks 4, 7, 8

  **References**:
  - `agent_sessions/main.py` - CLI entry point
  - Existing CLI commands in README.md

  **Acceptance Criteria**:
  - [ ] `agent-sessions --reindex` performs full reindex with progress
  - [ ] `agent-sessions --generate-embeddings` generates embeddings for all chunks
  - [ ] `agent-sessions --stats` shows session/message/chunk counts
  - [ ] `agent-sessions --search "query"` uses hybrid search
  - [ ] All commands have helpful output

  **Commit**: YES
  - Message: `feat(cli): add index management commands`
  - Files: `agent_sessions/main.py`

---

- [ ] 10. Update docs/SQLITE_INDEX_PLAN.md

  **What to do**:
  - Replace current plan with final implemented schema
  - Document all tables and their purposes
  - Document CLI commands
  - Document performance characteristics
  - Add usage examples

  **Must NOT do**:
  - No implementation details that might become stale

  **Recommended Agent Profile**:
  - **Category**: `writing`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 4 (final)
  - **Blocks**: None
  - **Blocked By**: All previous tasks

  **References**:
  - This plan document
  - Implemented code

  **Acceptance Criteria**:
  - [ ] Schema documented with all tables
  - [ ] CLI commands documented with examples
  - [ ] Performance expectations documented
  - [ ] Migration path documented

  **Commit**: YES
  - Message: `docs: update SQLITE_INDEX_PLAN with final implementation`
  - Files: `docs/SQLITE_INDEX_PLAN.md`

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1 | `feat(index): add SessionDatabase with SQLite schema and FTS5` | index/__init__.py, index/database.py |
| 2 | `feat(index): add AutoTagger for pattern-based tag generation` | index/tagger.py |
| 3 | `feat(index): add SessionChunker with turn-based chunking strategy` | index/chunker.py |
| 4 | `feat(index): add SessionIndexer with full and incremental indexing` | index/indexer.py |
| 5 | `feat(index): add EmbeddingGenerator with OpenAI integration` | index/embeddings.py |
| 6 | `feat(providers): add discover_sessions_fast and get_session_messages` | providers/*.py |
| 7 | `feat(index): add HybridSearch combining FTS5 and semantic search` | index/search.py, index/database.py |
| 8 | `feat(app): integrate SQLite index for fast loading and search` | app.py, ui/widgets.py |
| 9 | `feat(cli): add index management commands` | main.py |
| 10 | `docs: update SQLITE_INDEX_PLAN with final implementation` | docs/SQLITE_INDEX_PLAN.md |

---

## Success Criteria

### Verification Commands
```bash
# Full reindex
agent-sessions --reindex
# Expected: Progress bar, "Indexed X sessions, Y messages, Z chunks"

# Stats
agent-sessions --stats
# Expected: Session counts by harness, message count, chunk count, DB size

# FTS search
agent-sessions --search "prisma migration"
# Expected: Results in <100ms

# Semantic search
agent-sessions --search "agent-do excel ninety.io automation"
# Expected: Finds relevant session even without exact keywords

# Generate embeddings
agent-sessions --generate-embeddings
# Expected: Progress bar, "Generated embeddings for X chunks"
```

### Final Checklist
- [ ] All existing functionality preserved (no regressions)
- [ ] Startup time <2s with 80k+ sessions
- [ ] FTS search <100ms
- [ ] Semantic search finds sessions by intent, not just keywords
- [ ] Auto-tags provide useful filtering
- [ ] Works without OpenAI API key (graceful degradation)
- [ ] Incremental updates <500ms typical
