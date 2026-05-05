"""Tests for indexed hybrid search behavior."""

from datetime import datetime
from types import SimpleNamespace

import agent_sessions.index as index_module
import agent_sessions.main as main_module
from agent_sessions.index.database import ChunkRow, MessageRow, SessionDatabase
from agent_sessions.index.embeddings import EMBEDDING_DIMENSIONS, EmbeddingGenerator
from agent_sessions.index.search import HybridSearch, parse_hybrid_query


class _NoopEmbedder:
    @property
    def available(self) -> bool:
        return False

    def embed_query(self, query: str):
        return None


class _VectorEmbedder:
    @property
    def available(self) -> bool:
        return True

    def embed_query(self, query: str):
        return [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def _db(tmp_path):
    SessionDatabase.reset_instance()
    return SessionDatabase(tmp_path / "sessions.db")


def _add_session(
    db: SessionDatabase,
    session_id: str,
    *,
    harness: str = "codex",
    project_name: str = "api",
    timestamp: int | None = None,
    message: str = "Built auth token refresh middleware",
):
    ts = timestamp or int(datetime(2026, 1, 1).timestamp())
    db.upsert_session(
        session_id=session_id,
        harness=harness,
        timestamp=ts,
        project_path=f"/tmp/{project_name}",
        project_name=project_name,
        first_prompt_preview=message,
        file_path=f"/tmp/{session_id}.jsonl",
        file_mtime=ts,
        indexed_at=ts,
    )
    db.upsert_messages(
        [
            MessageRow(
                id=f"{session_id}-msg-1",
                session_id=session_id,
                role="user",
                content=message,
                timestamp=ts,
                sequence=0,
                has_code=False,
                tool_mentions=None,
            )
        ]
    )


def test_parse_hybrid_query_extracts_topic_from_natural_language():
    parsed = parse_hybrid_query(
        "find me the sessions where we worked on AI natural search"
    )

    assert parsed.text == "AI natural search"
    assert parsed.has_filters is False


def test_natural_language_search_uses_topic_not_instruction_words(tmp_path):
    db = _db(tmp_path)
    _add_session(db, "match", message="Built auth token refresh middleware")
    _add_session(db, "miss", message="Documented deployment checklist")

    search = HybridSearch(db, embedder=_NoopEmbedder())
    results = search.search(
        "find me the sessions where we worked on auth token refresh",
        limit=10,
    )

    assert [r.session_id for r in results] == ["match"]
    assert results[0].match_source in {"keyword", "metadata"}
    assert "auth token refresh" in results[0].match_snippet
    SessionDatabase.reset_instance()


def test_hybrid_search_applies_documented_metadata_filters(tmp_path):
    db = _db(tmp_path)
    _add_session(db, "codex-api", harness="codex", project_name="api")
    _add_session(db, "claude-api", harness="claude-code", project_name="api")
    _add_session(db, "codex-web", harness="codex", project_name="webapp")

    search = HybridSearch(db, embedder=_NoopEmbedder())

    by_harness = search.search("harness:codex auth token", limit=10)
    assert {r.session_id for r in by_harness} == {"codex-api", "codex-web"}

    by_project = search.search("project:webapp auth token", limit=10)
    assert [r.session_id for r in by_project] == ["codex-web"]

    filter_only = search.search("harness:claude-code project:api", limit=10)
    assert [r.session_id for r in filter_only] == ["claude-api"]

    SessionDatabase.reset_instance()


def test_hybrid_search_applies_date_and_tag_filters(tmp_path):
    db = _db(tmp_path)
    old_ts = int(datetime(2024, 1, 1).timestamp())
    new_ts = int(datetime(2026, 1, 1).timestamp())
    _add_session(db, "old", timestamp=old_ts)
    _add_session(db, "new", timestamp=new_ts)
    db.upsert_annotations(
        "new",
        [{"ts": "2026-01-01T00:00:00", "type": "tag", "value": "breakthrough"}],
    )

    search = HybridSearch(db, embedder=_NoopEmbedder())

    after_results = search.search("after:2025-01-01 auth token", limit=10)
    assert [r.session_id for r in after_results] == ["new"]

    tag_results = search.search("#tag:breakthrough auth token", limit=10)
    assert [r.session_id for r in tag_results] == ["new"]

    SessionDatabase.reset_instance()


def test_database_can_select_chunks_missing_embeddings(tmp_path):
    db = _db(tmp_path)
    _add_session(db, "session")
    db.upsert_chunks(
        [
            ChunkRow(
                id=None,
                session_id="session",
                message_id=None,
                chunk_index=0,
                chunk_type="summary",
                content="Needs embedding",
                metadata="{}",
                embedding=None,
                embedding_model=None,
                created_at=None,
            ),
            ChunkRow(
                id=None,
                session_id="session",
                message_id=None,
                chunk_index=1,
                chunk_type="turn",
                content="Already embedded",
                metadata="{}",
                embedding=b"1234",
                embedding_model="text-embedding-3-small",
                created_at=None,
            ),
        ]
    )

    missing = db.get_chunks_without_embeddings()

    assert len(missing) == 1
    assert missing[0].content == "Needs embedding"
    SessionDatabase.reset_instance()


def test_semantic_search_result_includes_best_chunk_snippet(tmp_path):
    db = _db(tmp_path)
    _add_session(
        db,
        "semantic-match",
        message="Unrelated deployment checklist",
    )
    db.upsert_chunks(
        [
            ChunkRow(
                id=None,
                session_id="semantic-match",
                message_id=None,
                chunk_index=0,
                chunk_type="turn",
                content="Discussed calendar sync retry strategy and webhook replay handling",
                metadata="{}",
                embedding=EmbeddingGenerator.serialize_embedding(
                    [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
                ),
                embedding_model="text-embedding-3-small",
                created_at=None,
            )
        ]
    )

    search = HybridSearch(db, embedder=_VectorEmbedder())
    results = search.search("calendar sync retries", limit=10)

    assert [r.session_id for r in results] == ["semantic-match"]
    assert results[0].match_source == "semantic"
    assert "calendar sync retry strategy" in results[0].match_snippet
    SessionDatabase.reset_instance()


def test_generate_embeddings_command_backfills_missing_chunks(
    tmp_path, monkeypatch, capsys
):
    db = _db(tmp_path)
    _add_session(db, "session")
    db.upsert_chunks(
        [
            ChunkRow(
                id=None,
                session_id="session",
                message_id=None,
                chunk_index=0,
                chunk_type="summary",
                content="Needs embedding",
                metadata="{}",
                embedding=None,
                embedding_model=None,
                created_at=None,
            )
        ]
    )

    class _FakeEmbeddingGenerator:
        available = True

        def embed_chunks(self, chunks):
            for chunk in chunks:
                chunk.embedding = b"embedded"
            return chunks

    monkeypatch.setattr(index_module, "SessionDatabase", lambda: db)
    monkeypatch.setattr(index_module, "EmbeddingGenerator", _FakeEmbeddingGenerator)

    main_module.cmd_generate_embeddings(SimpleNamespace())

    rows = db.get_session_chunks("session")
    assert rows[0].embedding == b"embedded"
    assert rows[0].embedding_model == "text-embedding-3-small"
    assert "Generated embeddings for 1 chunks" in capsys.readouterr().out
    SessionDatabase.reset_instance()
