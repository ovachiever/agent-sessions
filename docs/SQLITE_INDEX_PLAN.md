# SQLite Session Index with Full Content Storage

## Overview

Replace file-scanning with a SQLite database that stores all session metadata and full message content. Enables fast queries, full-text search, and efficient incremental updates.

## Database Location

```
~/.cache/agent-sessions/sessions.db
```

## Schema

```sql
-- Index metadata
CREATE TABLE index_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Keys: schema_version, last_full_index, etc.

-- Sessions table
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- ses_xxx or session filename
    harness TEXT NOT NULL,         -- 'claude_code', 'opencode', 'droid'
    project_path TEXT,
    project_name TEXT,
    timestamp INTEGER NOT NULL,    -- Unix timestamp
    is_child BOOLEAN DEFAULT FALSE,
    parent_id TEXT,
    child_type TEXT,               -- 'worker', 'prometheus', etc.
    message_count INTEGER DEFAULT 0,
    first_prompt_preview TEXT,     -- First 500 chars for display
    file_path TEXT,                -- Original file path for reference
    file_mtime INTEGER,            -- File modification time for change detection
    indexed_at INTEGER,            -- When we last indexed this session
    FOREIGN KEY (parent_id) REFERENCES sessions(id)
);

CREATE INDEX idx_sessions_harness ON sessions(harness);
CREATE INDEX idx_sessions_timestamp ON sessions(timestamp DESC);
CREATE INDEX idx_sessions_parent ON sessions(parent_id);
CREATE INDEX idx_sessions_project ON sessions(project_path);

-- Messages table
CREATE TABLE messages (
    id TEXT PRIMARY KEY,           -- msg_xxx or generated
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,            -- 'user', 'assistant', 'system'
    content TEXT,
    timestamp INTEGER,
    sequence INTEGER,              -- Order within session
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX idx_messages_session ON messages(session_id);

-- Full-text search
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content_rowid='rowid'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (NEW.rowid, NEW.content);
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', OLD.rowid, OLD.content);
END;
```

## File Structure

```
agent-sessions/
  agent_sessions/
    index/
      __init__.py
      database.py      # SessionDatabase class
      indexer.py       # Indexing logic
      models.py        # Pydantic models for DB rows
    providers/
      base.py          # Update to query DB instead of files
      ...
```

## Core Classes

### SessionDatabase (database.py)

```python
class SessionDatabase:
    """SQLite database for session index."""
    
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.conn = sqlite3.connect(self.db_path)
        self._ensure_schema()
    
    # Session queries
    def get_sessions(self, harness: str = None, limit: int = None) -> list[Session]
    def get_session(self, session_id: str) -> Session | None
    def get_children(self, parent_id: str) -> list[Session]
    def get_parents(self, harness: str = None) -> list[Session]
    
    # Message queries  
    def get_messages(self, session_id: str) -> list[Message]
    def search_messages(self, query: str, limit: int = 100) -> list[SearchResult]
    
    # Indexing
    def upsert_session(self, session: Session) -> None
    def upsert_messages(self, session_id: str, messages: list[Message]) -> None
    def delete_session(self, session_id: str) -> None
    def get_indexed_sessions(self, harness: str) -> dict[str, IndexedSession]
```

### SessionIndexer (indexer.py)

```python
class SessionIndexer:
    """Indexes sessions from filesystem into database."""
    
    def __init__(self, db: SessionDatabase):
        self.db = db
        self.providers = [ClaudeCodeProvider(), OpenCodeProvider(), DroidProvider()]
    
    def full_reindex(self, progress_callback=None) -> IndexStats:
        """Full reindex of all sessions. Run once or on-demand."""
        
    def incremental_update(self) -> IndexStats:
        """Fast update - only new/changed sessions."""
        
    def index_harness(self, harness: str) -> IndexStats:
        """Reindex a specific harness."""
```

## Incremental Update Algorithm

```python
def incremental_update(self) -> IndexStats:
    """
    Called on every app load. Fast path for typical usage.
    
    Strategy:
    1. Get all session dirs from filesystem with their mtimes
    2. Get all indexed sessions from DB with their indexed mtimes
    3. Compare:
       - New dirs (not in DB) → index
       - Changed dirs (mtime > indexed_at) → re-index
       - Unchanged → skip
       - Deleted (in DB but not filesystem) → optionally remove
    """
    stats = IndexStats()
    
    for provider in self.providers:
        # Get filesystem state
        fs_sessions = provider.discover_sessions_fast()  # Returns {id: mtime}
        
        # Get DB state
        db_sessions = self.db.get_indexed_sessions(provider.name)  # Returns {id: indexed_at}
        
        # Find new sessions
        new_ids = set(fs_sessions.keys()) - set(db_sessions.keys())
        for session_id in new_ids:
            self._index_session(provider, session_id)
            stats.added += 1
        
        # Find changed sessions (mtime > indexed_at)
        for session_id, mtime in fs_sessions.items():
            if session_id in db_sessions:
                if mtime > db_sessions[session_id].indexed_at:
                    self._reindex_session(provider, session_id)
                    stats.updated += 1
        
        # Optionally handle deleted sessions
        deleted_ids = set(db_sessions.keys()) - set(fs_sessions.keys())
        for session_id in deleted_ids:
            self.db.delete_session(session_id)
            stats.deleted += 1
    
    return stats
```

## Provider Changes

```python
# base.py - New methods for indexing
class SessionProvider:
    def discover_sessions_fast(self) -> dict[str, int]:
        """
        Return {session_id: mtime} without parsing content.
        Used for incremental indexing.
        """
        raise NotImplementedError
    
    def parse_session_full(self, session_id: str) -> tuple[Session, list[Message]]:
        """
        Parse session and all messages for indexing.
        """
        raise NotImplementedError
```

## App Integration

```python
# app.py
class AgentSessionsApp(App):
    def on_mount(self):
        # Fast incremental update on every load
        self.run_worker(self._update_index)
    
    async def _update_index(self):
        db = SessionDatabase()
        indexer = SessionIndexer(db)
        
        # Show status
        self.status = "Checking for new sessions..."
        stats = indexer.incremental_update()
        
        if stats.added or stats.updated:
            self.status = f"Indexed {stats.added} new, {stats.updated} updated"
        
        # Now load from DB (fast!)
        self._populate_from_db()
    
    def _populate_from_db(self):
        """Load sessions from DB instead of scanning files."""
        db = SessionDatabase()
        for harness in ['claude_code', 'opencode', 'droid']:
            parents = db.get_parents(harness)
            # ... populate UI
```

## CLI Commands

```bash
# Full reindex (run manually if needed)
agent-sessions --reindex

# Search across all sessions
agent-sessions --search "prisma migration"

# Stats
agent-sessions --stats
# Output: 81,435 sessions, 245,892 messages, DB size: 847MB
```

## Performance Expectations

| Operation | Time |
|-----------|------|
| Initial full index (81k sessions) | 2-5 minutes |
| Incremental update (typical) | 100-500ms |
| Incremental update (100 new sessions) | 2-5 seconds |
| Query parents by harness | <10ms |
| Full-text search | <100ms |
| Load session messages | <10ms |

## Implementation Phases

### Phase 1: Database Foundation
- Create `database.py` with schema and basic CRUD
- Create `indexer.py` with full reindex
- Test with small dataset

### Phase 2: Provider Integration
- Add `discover_sessions_fast()` to each provider
- Add `parse_session_full()` to each provider
- Implement incremental update

### Phase 3: App Integration
- Modify `app.py` to use DB queries
- Add progress indicator for indexing
- Add `--reindex` CLI flag

### Phase 4: Search
- Add search UI (Ctrl+F)
- Implement FTS5 queries
- Show search results

## Future Enhancements

### Semantic Search with Embeddings

```sql
-- Add embeddings table
CREATE TABLE embeddings (
    message_id TEXT PRIMARY KEY,
    vector BLOB,  -- Serialized float32 array
    model TEXT,   -- e.g., 'text-embedding-3-small'
    FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
);
```

### AI Summaries Integration

```sql
-- Add summaries table (migrate from current cache)
CREATE TABLE summaries (
    session_id TEXT PRIMARY KEY,
    summary TEXT,
    model TEXT,
    content_hash TEXT,  -- For cache invalidation
    created_at INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```
