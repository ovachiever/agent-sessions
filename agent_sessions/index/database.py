import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".cache" / "agent-sessions" / "sessions.db"


@dataclass
class SessionRow:
    id: str
    harness: str
    project_path: Optional[str]
    project_name: Optional[str]
    timestamp: int
    timestamp_end: Optional[int]
    is_child: bool
    parent_id: Optional[str]
    child_type: Optional[str]
    message_count: int
    turn_count: int
    first_prompt_preview: Optional[str]
    file_path: Optional[str]
    file_mtime: Optional[int]
    indexed_at: Optional[int]
    auto_tags: Optional[str]


@dataclass
class MessageRow:
    id: str
    session_id: str
    role: str
    content: Optional[str]
    timestamp: Optional[int]
    sequence: int
    has_code: bool
    tool_mentions: Optional[str]


@dataclass
class ChunkRow:
    id: Optional[int]
    session_id: str
    message_id: Optional[str]
    chunk_index: int
    chunk_type: str
    content: str
    metadata: Optional[str]
    embedding: Optional[bytes]
    embedding_model: Optional[str]
    created_at: Optional[int]


class SessionDatabase:
    _instance: Optional["SessionDatabase"] = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[Path] = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._db_path = db_path or DEFAULT_DB_PATH
                    instance._connection: Optional[sqlite3.Connection] = None
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._lock:
            if cls._instance is not None:
                if cls._instance._connection:
                    cls._instance._connection.close()
                cls._instance = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
        return self._connection

    def _ensure_schema(self):
        if self._initialized:
            return
        conn = self._get_connection()
        current_version = self._get_schema_version(conn)
        if current_version < SCHEMA_VERSION:
            self._create_schema(conn)
            self._set_schema_version(conn, SCHEMA_VERSION)
        self._initialized = True

    def _get_schema_version(self, conn: sqlite3.Connection) -> int:
        try:
            row = conn.execute(
                "SELECT MAX(version) as v FROM schema_meta"
            ).fetchone()
            return row["v"] if row and row["v"] else 0
        except sqlite3.OperationalError:
            return 0

    def _set_schema_version(self, conn: sqlite3.Connection, version: int):
        conn.execute(
            "INSERT INTO schema_meta (version, description) VALUES (?, ?)",
            (version, f"Schema version {version}"),
        )

    def _create_schema(self, conn: sqlite3.Connection):
        conn.executescript(self._get_schema_sql())
        conn.executescript(self._get_fts_sql())
        conn.executescript(self._get_triggers_sql())

    def _get_schema_sql(self) -> str:
        return """
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER PRIMARY KEY,
                applied_at INTEGER DEFAULT (strftime('%s', 'now')),
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                harness TEXT NOT NULL,
                project_path TEXT,
                project_name TEXT,
                timestamp INTEGER NOT NULL,
                timestamp_end INTEGER,
                is_child INTEGER DEFAULT 0,
                parent_id TEXT,
                child_type TEXT,
                message_count INTEGER DEFAULT 0,
                turn_count INTEGER DEFAULT 0,
                first_prompt_preview TEXT,
                file_path TEXT,
                file_mtime INTEGER,
                indexed_at INTEGER,
                auto_tags TEXT,
                FOREIGN KEY (parent_id) REFERENCES sessions(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_harness ON sessions(harness);
            CREATE INDEX IF NOT EXISTS idx_sessions_project_path ON sessions(project_path);
            CREATE INDEX IF NOT EXISTS idx_sessions_timestamp ON sessions(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_sessions_parent_id ON sessions(parent_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_is_child ON sessions(is_child);

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                timestamp INTEGER,
                sequence INTEGER,
                has_code INTEGER DEFAULT 0,
                tool_mentions TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_sequence ON messages(session_id, sequence);

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id TEXT,
                chunk_index INTEGER NOT NULL,
                chunk_type TEXT DEFAULT 'turn',
                content TEXT NOT NULL,
                metadata TEXT,
                embedding BLOB,
                embedding_model TEXT,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_session_id ON chunks(session_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_type ON chunks(chunk_type);

            CREATE TABLE IF NOT EXISTS semantic_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                result_count INTEGER,
                top_session_ids TEXT,
                search_time_ms INTEGER,
                timestamp INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS project_stats (
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

            CREATE TABLE IF NOT EXISTS summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT,
                model TEXT,
                content_hash TEXT,
                created_at INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
        """

    def _get_fts_sql(self) -> str:
        return """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='rowid',
                tokenize='porter unicode61 remove_diacritics 1'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                first_prompt_preview,
                project_name,
                auto_tags,
                content='sessions',
                content_rowid='rowid',
                tokenize='porter unicode61'
            );
        """

    def _get_triggers_sql(self) -> str:
        return """
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content)
                VALUES (NEW.rowid, NEW.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', OLD.rowid, OLD.content);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', OLD.rowid, OLD.content);
                INSERT INTO messages_fts(rowid, content)
                VALUES (NEW.rowid, NEW.content);
            END;

            CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
                INSERT INTO sessions_fts(rowid, first_prompt_preview, project_name, auto_tags)
                VALUES (NEW.rowid, NEW.first_prompt_preview, NEW.project_name, NEW.auto_tags);
            END;

            CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, first_prompt_preview, project_name, auto_tags)
                VALUES ('delete', OLD.rowid, OLD.first_prompt_preview, OLD.project_name, OLD.auto_tags);
            END;

            CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, first_prompt_preview, project_name, auto_tags)
                VALUES ('delete', OLD.rowid, OLD.first_prompt_preview, OLD.project_name, OLD.auto_tags);
                INSERT INTO sessions_fts(rowid, first_prompt_preview, project_name, auto_tags)
                VALUES (NEW.rowid, NEW.first_prompt_preview, NEW.project_name, NEW.auto_tags);
            END;
        """

    def initialize(self):
        self._ensure_schema()

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None
            self._initialized = False

    def upsert_session(
        self,
        session_id: str,
        harness: str,
        timestamp: int,
        *,
        project_path: Optional[str] = None,
        project_name: Optional[str] = None,
        timestamp_end: Optional[int] = None,
        is_child: bool = False,
        parent_id: Optional[str] = None,
        child_type: Optional[str] = None,
        message_count: int = 0,
        turn_count: int = 0,
        first_prompt_preview: Optional[str] = None,
        file_path: Optional[str] = None,
        file_mtime: Optional[int] = None,
        indexed_at: Optional[int] = None,
        auto_tags: Optional[list[str]] = None,
    ) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        tags_json = json.dumps(auto_tags) if auto_tags else None
        conn.execute(
            """
            INSERT INTO sessions (
                id, harness, project_path, project_name, timestamp, timestamp_end,
                is_child, parent_id, child_type, message_count, turn_count,
                first_prompt_preview, file_path, file_mtime, indexed_at, auto_tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                harness = excluded.harness,
                project_path = excluded.project_path,
                project_name = excluded.project_name,
                timestamp = excluded.timestamp,
                timestamp_end = excluded.timestamp_end,
                is_child = excluded.is_child,
                parent_id = excluded.parent_id,
                child_type = excluded.child_type,
                message_count = excluded.message_count,
                turn_count = excluded.turn_count,
                first_prompt_preview = excluded.first_prompt_preview,
                file_path = excluded.file_path,
                file_mtime = excluded.file_mtime,
                indexed_at = excluded.indexed_at,
                auto_tags = excluded.auto_tags
            """,
            (
                session_id,
                harness,
                project_path,
                project_name,
                timestamp,
                timestamp_end,
                1 if is_child else 0,
                parent_id,
                child_type,
                message_count,
                turn_count,
                first_prompt_preview,
                file_path,
                file_mtime,
                indexed_at,
                tags_json,
            ),
        )

    def upsert_messages(self, messages: list[MessageRow]) -> None:
        if not messages:
            return
        self._ensure_schema()
        conn = self._get_connection()
        conn.executemany(
            """
            INSERT INTO messages (
                id, session_id, role, content, timestamp, sequence, has_code, tool_mentions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                content = excluded.content,
                timestamp = excluded.timestamp,
                sequence = excluded.sequence,
                has_code = excluded.has_code,
                tool_mentions = excluded.tool_mentions
            """,
            [
                (
                    m.id,
                    m.session_id,
                    m.role,
                    m.content,
                    m.timestamp,
                    m.sequence,
                    1 if m.has_code else 0,
                    m.tool_mentions,
                )
                for m in messages
            ],
        )

    def delete_session(self, session_id: str) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def delete_messages_for_session(self, session_id: str) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def delete_chunks_for_session(self, session_id: str) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute("DELETE FROM chunks WHERE session_id = ?", (session_id,))

    def upsert_chunks(self, chunks: list[ChunkRow]) -> None:
        if not chunks:
            return
        self._ensure_schema()
        conn = self._get_connection()
        for chunk in chunks:
            if chunk.id:
                conn.execute(
                    """
                    UPDATE chunks SET
                        session_id = ?, message_id = ?, chunk_index = ?,
                        chunk_type = ?, content = ?, metadata = ?,
                        embedding = ?, embedding_model = ?
                    WHERE id = ?
                    """,
                    (
                        chunk.session_id,
                        chunk.message_id,
                        chunk.chunk_index,
                        chunk.chunk_type,
                        chunk.content,
                        chunk.metadata,
                        chunk.embedding,
                        chunk.embedding_model,
                        chunk.id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO chunks (
                        session_id, message_id, chunk_index, chunk_type,
                        content, metadata, embedding, embedding_model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.session_id,
                        chunk.message_id,
                        chunk.chunk_index,
                        chunk.chunk_type,
                        chunk.content,
                        chunk.metadata,
                        chunk.embedding,
                        chunk.embedding_model,
                    ),
                )

    def get_session(self, session_id: str) -> Optional[SessionRow]:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def get_sessions(
        self,
        *,
        harness: Optional[str] = None,
        project_path: Optional[str] = None,
        is_child: Optional[bool] = None,
        limit: int = 1000,
        offset: int = 0,
    ):
        from ..models import Session
        
        self._ensure_schema()
        conn = self._get_connection()
        conditions = []
        params: list = []

        if harness:
            conditions.append("s.harness = ?")
            params.append(harness)
        if project_path:
            conditions.append("s.project_path = ?")
            params.append(project_path)
        if is_child is not None:
            conditions.append("s.is_child = ?")
            params.append(1 if is_child else 0)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.extend([limit, offset])
        rows = conn.execute(
            f"""
            SELECT s.*, sm.summary as _summary
            FROM sessions s
            LEFT JOIN summaries sm ON s.id = sm.session_id
            {where_clause}
            ORDER BY s.timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()

        return [self._sessionrow_to_session(self._row_to_session(r), summary=r["_summary"]) for r in rows]

    def get_all_sessions(self):
        """Get all sessions without filters.
        
        Limit is high enough to capture all parent sessions even when
        the DB has many child/sub-agent sessions that sort first by recency.
        """
        return self.get_sessions(limit=100000)

    def get_session_rows(self, *, limit: int = 100000) -> list[SessionRow]:
        """Get raw SessionRow objects (for indexer mtime checks)."""
        self._ensure_schema()
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_parents(self, harness: Optional[str] = None):
        return self.get_sessions(harness=harness, is_child=False)

    def get_children(
        self, parent_id: str, harness: Optional[str] = None
    ) -> list[SessionRow]:
        self._ensure_schema()
        conn = self._get_connection()
        conditions = ["parent_id = ?"]
        params: list = [parent_id]

        if harness:
            conditions.append("harness = ?")
            params.append(harness)

        where_clause = "WHERE " + " AND ".join(conditions)
        rows = conn.execute(
            f"""
            SELECT * FROM sessions
            {where_clause}
            ORDER BY timestamp DESC
            """,
            params,
        ).fetchall()

        return [self._row_to_session(r) for r in rows]

    def get_session_messages(self, session_id: str) -> list[MessageRow]:
        self._ensure_schema()
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY sequence
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def get_last_assistant_response(self, session_id: str) -> Optional[str]:
        """Get the last assistant message content for a session."""
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id = ? AND role = 'assistant'
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return row["content"] if row else None

    def get_session_chunks(self, session_id: str) -> list[ChunkRow]:
        self._ensure_schema()
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT * FROM chunks
            WHERE session_id = ?
            ORDER BY chunk_index
            """,
            (session_id,),
        ).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def get_index_meta(self, key: str) -> Optional[str]:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute(
            "SELECT value FROM index_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_index_meta(self, key: str, value: str) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO index_meta (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def upsert_summary(
        self,
        session_id: str,
        summary: str,
        model: str,
        content_hash: str,
        created_at: int,
    ) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO summaries (session_id, summary, model, content_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                summary = excluded.summary,
                model = excluded.model,
                content_hash = excluded.content_hash,
                created_at = excluded.created_at
            """,
            (session_id, summary, model, content_hash, created_at),
        )

    def get_summary(self, session_id: str) -> Optional[tuple[str, str]]:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute(
            "SELECT summary, content_hash FROM summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return (row["summary"], row["content_hash"]) if row else None

    def update_project_stats(
        self,
        project_path: str,
        *,
        project_name: Optional[str] = None,
        total_sessions: int = 0,
        parent_sessions: int = 0,
        child_sessions: int = 0,
        first_session_time: Optional[int] = None,
        last_session_time: Optional[int] = None,
        harness_counts: Optional[dict] = None,
        total_messages: int = 0,
        common_tags: Optional[list[str]] = None,
        updated_at: Optional[int] = None,
    ) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        harness_json = json.dumps(harness_counts) if harness_counts else None
        tags_json = json.dumps(common_tags) if common_tags else None
        conn.execute(
            """
            INSERT INTO project_stats (
                project_path, project_name, total_sessions, parent_sessions,
                child_sessions, first_session_time, last_session_time,
                harness_counts, total_messages, common_tags, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_path) DO UPDATE SET
                project_name = excluded.project_name,
                total_sessions = excluded.total_sessions,
                parent_sessions = excluded.parent_sessions,
                child_sessions = excluded.child_sessions,
                first_session_time = excluded.first_session_time,
                last_session_time = excluded.last_session_time,
                harness_counts = excluded.harness_counts,
                total_messages = excluded.total_messages,
                common_tags = excluded.common_tags,
                updated_at = excluded.updated_at
            """,
            (
                project_path,
                project_name,
                total_sessions,
                parent_sessions,
                child_sessions,
                first_session_time,
                last_session_time,
                harness_json,
                total_messages,
                tags_json,
                updated_at,
            ),
        )

    def log_semantic_search(
        self,
        query: str,
        result_count: int,
        top_session_ids: list[str],
        search_time_ms: int,
    ) -> None:
        self._ensure_schema()
        conn = self._get_connection()
        conn.execute(
            """
            INSERT INTO semantic_searches (query, result_count, top_session_ids, search_time_ms)
            VALUES (?, ?, ?, ?)
            """,
            (query, result_count, json.dumps(top_session_ids), search_time_ms),
        )

    def count_sessions(self, harness: Optional[str] = None) -> int:
        self._ensure_schema()
        conn = self._get_connection()
        if harness:
            row = conn.execute(
                "SELECT COUNT(*) as c FROM sessions WHERE harness = ?", (harness,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()
        return row["c"] if row else 0

    def count_messages(self) -> int:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute("SELECT COUNT(*) as c FROM messages").fetchone()
        return row["c"] if row else 0

    def count_chunks(self) -> int:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute("SELECT COUNT(*) as c FROM chunks").fetchone()
        return row["c"] if row else 0

    def _row_to_session(self, row: sqlite3.Row) -> SessionRow:
        return SessionRow(
            id=row["id"],
            harness=row["harness"],
            project_path=row["project_path"],
            project_name=row["project_name"],
            timestamp=row["timestamp"],
            timestamp_end=row["timestamp_end"],
            is_child=bool(row["is_child"]),
            parent_id=row["parent_id"],
            child_type=row["child_type"],
            message_count=row["message_count"],
            turn_count=row["turn_count"],
            first_prompt_preview=row["first_prompt_preview"],
            file_path=row["file_path"],
            file_mtime=row["file_mtime"],
            indexed_at=row["indexed_at"],
            auto_tags=row["auto_tags"],
        )

    def _sessionrow_to_session(self, row: SessionRow, summary: Optional[str] = None):
        """Convert SessionRow namedtuple to Session dataclass."""
        from ..models import Session
        
        return Session(
            id=row.id,
            harness=row.harness,
            raw_path=Path(row.file_path) if row.file_path else Path(),
            project_path=Path(row.project_path) if row.project_path else Path(),
            project_name=row.project_name or "",
            title="",
            first_prompt=row.first_prompt_preview or "",
            last_prompt="",
            last_response="",
            created_time=datetime.fromtimestamp(row.timestamp) if row.timestamp else None,
            modified_time=datetime.fromtimestamp(row.timestamp_end) if row.timestamp_end else None,
            is_child=row.is_child,
            child_type=row.child_type or "",
            parent_id=row.parent_id,
            model="",
            tool_calls=[],
            tokens_used=None,
            summary=summary,
            content_hash="",
            extra={},
        )

    def _row_to_message(self, row: sqlite3.Row) -> MessageRow:
        return MessageRow(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            timestamp=row["timestamp"],
            sequence=row["sequence"],
            has_code=bool(row["has_code"]),
            tool_mentions=row["tool_mentions"],
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> ChunkRow:
        return ChunkRow(
            id=row["id"],
            session_id=row["session_id"],
            message_id=row["message_id"],
            chunk_index=row["chunk_index"],
            chunk_type=row["chunk_type"],
            content=row["content"],
            metadata=row["metadata"],
            embedding=row["embedding"],
            embedding_model=row["embedding_model"],
            created_at=row["created_at"],
        )

    def search_messages_fts(
        self, query: str, limit: int = 100
    ) -> list[tuple[str, float]]:
        """Search messages via FTS5. Returns (session_id, bm25_score) - lower scores = more relevant."""
        self._ensure_schema()
        conn = self._get_connection()
        safe_query = query.replace('"', '""')
        rows = conn.execute(
            """
            SELECT m.session_id, bm25(messages_fts) as score
            FROM messages_fts
            JOIN messages m ON messages_fts.rowid = m.rowid
            WHERE messages_fts MATCH ?
            GROUP BY m.session_id
            ORDER BY score
            LIMIT ?
            """,
            (f'"{safe_query}"', limit),
        ).fetchall()
        return [(row["session_id"], row["score"]) for row in rows]

    def search_sessions_fts(
        self, query: str, limit: int = 100
    ) -> list[tuple[str, float]]:
        """Search session metadata via FTS5. Returns (session_id, bm25_score) - lower scores = more relevant."""
        self._ensure_schema()
        conn = self._get_connection()
        safe_query = query.replace('"', '""')
        rows = conn.execute(
            """
            SELECT s.id, bm25(sessions_fts) as score
            FROM sessions_fts
            JOIN sessions s ON sessions_fts.rowid = s.rowid
            WHERE sessions_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (f'"{safe_query}"', limit),
        ).fetchall()
        return [(row["id"], row["score"]) for row in rows]

    def get_all_chunk_embeddings(self) -> list[tuple[str, int, bytes]]:
        self._ensure_schema()
        conn = self._get_connection()
        rows = conn.execute(
            """
            SELECT session_id, id, embedding
            FROM chunks
            WHERE embedding IS NOT NULL
            ORDER BY session_id, chunk_index
            """
        ).fetchall()
        return [(row["session_id"], row["id"], row["embedding"]) for row in rows]

    def count_chunks_with_embeddings(self) -> int:
        self._ensure_schema()
        conn = self._get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as c FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()
        return row["c"] if row else 0
