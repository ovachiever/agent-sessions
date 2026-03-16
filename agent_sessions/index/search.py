import re
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .database import SessionDatabase
from .embeddings import EmbeddingGenerator, EMBEDDING_DIMENSIONS


@dataclass
class SearchResult:
    session_id: str
    score: float
    fts_score: Optional[float] = None
    semantic_score: Optional[float] = None


class HybridSearch:
    def __init__(
        self,
        db: Optional[SessionDatabase] = None,
        embedder: Optional[EmbeddingGenerator] = None,
        fts_weight: float = 0.3,
        semantic_weight: float = 0.7,
    ):
        self._db = db or SessionDatabase()
        self._embedder = embedder or EmbeddingGenerator()
        self._fts_weight = fts_weight
        self._semantic_weight = semantic_weight
        # Vectorized cache: numpy matrix + parallel session_id list
        self._embedding_matrix: Optional[np.ndarray] = None
        self._embedding_norms: Optional[np.ndarray] = None
        self._chunk_session_ids: Optional[list[str]] = None

    def _load_embedding_cache(self):
        """Load embeddings from DB into a pre-normalized numpy matrix."""
        raw = self._db.get_all_chunk_embeddings()
        if not raw:
            self._embedding_matrix = np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
            self._embedding_norms = np.empty(0, dtype=np.float32)
            self._chunk_session_ids = []
            return

        session_ids = []
        # Deserialize all blobs into a contiguous float32 array
        flat = bytearray()
        for session_id, _chunk_id, blob in raw:
            session_ids.append(session_id)
            flat.extend(blob)

        n = len(session_ids)
        matrix = np.frombuffer(bytes(flat), dtype=np.float32).reshape(n, -1)

        # Pre-compute norms for cosine similarity
        norms = np.linalg.norm(matrix, axis=1)

        self._embedding_matrix = matrix
        self._embedding_norms = norms
        self._chunk_session_ids = session_ids

    @staticmethod
    def _extract_tag_filters(query: str) -> tuple[str, list[str]]:
        """Extract #tag:value patterns from query.

        Returns (remaining_query, list_of_tags).
        """
        tags = re.findall(r"#tag:(\S+)", query)
        remaining = re.sub(r"#tag:\S+", "", query).strip()
        return remaining, tags

    def search(
        self,
        query: str,
        limit: int = 50,
        fts_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
    ) -> list[SearchResult]:
        start_time = time.time()

        remaining_query, tag_filters = self._extract_tag_filters(query)

        # Resolve tag-filtered session IDs
        tag_session_ids: Optional[set[str]] = None
        if tag_filters:
            sets = [set(self._db.get_sessions_by_tag(t)) for t in tag_filters]
            tag_session_ids = sets[0]
            for s in sets[1:]:
                tag_session_ids &= s
            if not tag_session_ids:
                return []

        # If query is only tag filters, return all matching sessions
        if not remaining_query and tag_session_ids is not None:
            results = [
                SearchResult(session_id=sid, score=1.0)
                for sid in list(tag_session_ids)[:limit]
            ]
            elapsed_ms = int((time.time() - start_time) * 1000)
            top_ids = [r.session_id for r in results[:10]]
            self._db.log_semantic_search(query, len(results), top_ids, elapsed_ms)
            return results

        fts_w = fts_weight if fts_weight is not None else self._fts_weight
        sem_w = semantic_weight if semantic_weight is not None else self._semantic_weight

        search_query = remaining_query or query
        fts_results = self._search_fts(search_query, limit=limit * 2)
        semantic_results = self._search_semantic(search_query, limit=limit * 2)

        combined = self._combine_scores(fts_results, semantic_results, fts_w, sem_w)
        combined = [r for r in combined if r.score >= 0.2]

        # Apply tag filter to results
        if tag_session_ids is not None:
            combined = [r for r in combined if r.session_id in tag_session_ids]

        combined.sort(key=lambda r: r.score, reverse=True)
        results = combined[:limit]

        elapsed_ms = int((time.time() - start_time) * 1000)
        top_ids = [r.session_id for r in results[:10]]
        self._db.log_semantic_search(query, len(results), top_ids, elapsed_ms)

        return results

    def search_fts_only(self, query: str, limit: int = 50) -> list[SearchResult]:
        fts_results = self._search_fts(query, limit=limit)
        return [
            SearchResult(session_id=sid, score=score, fts_score=score)
            for sid, score in fts_results.items()
        ]

    def search_semantic_only(self, query: str, limit: int = 50) -> list[SearchResult]:
        semantic_results = self._search_semantic(query, limit=limit)
        return [
            SearchResult(session_id=sid, score=score, semantic_score=score)
            for sid, score in semantic_results.items()
        ]

    def _search_fts(self, query: str, limit: int) -> dict[str, float]:
        msg_results = self._db.search_messages_fts(query, limit=limit)
        sess_results = self._db.search_sessions_fts(query, limit=limit)

        scores: dict[str, float] = {}
        for session_id, bm25_score in msg_results:
            scores[session_id] = -bm25_score
        for session_id, bm25_score in sess_results:
            if session_id in scores:
                scores[session_id] = max(scores[session_id], -bm25_score)
            else:
                scores[session_id] = -bm25_score

        return self._normalize_scores(scores)

    def _search_semantic(self, query: str, limit: int) -> dict[str, float]:
        query_embedding = self._embedder.embed_query(query)
        if query_embedding is None:
            return {}

        if self._embedding_matrix is None:
            self._load_embedding_cache()

        matrix = self._embedding_matrix
        norms = self._embedding_norms
        session_ids = self._chunk_session_ids

        if matrix is None or norms is None or session_ids is None or len(session_ids) == 0:
            return {}

        MIN_COSINE = 0.35

        # Vectorized cosine similarity: dot(query, matrix^T) / (||query|| * ||rows||)
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return {}

        dots = matrix @ query_vec
        similarities = dots / (norms * query_norm)

        # Aggregate: best similarity per session
        session_best: dict[str, float] = {}
        for i, sim in enumerate(similarities):
            sid = session_ids[i]
            if sim >= MIN_COSINE:
                if sid not in session_best or sim > session_best[sid]:
                    session_best[sid] = float(sim)

        if not session_best:
            return {}

        sorted_sessions = sorted(session_best.items(), key=lambda x: x[1], reverse=True)
        top_sessions = dict(sorted_sessions[:limit])

        return self._normalize_scores(top_sessions)

    def _combine_scores(
        self,
        fts_scores: dict[str, float],
        semantic_scores: dict[str, float],
        fts_weight: float,
        semantic_weight: float,
    ) -> list[SearchResult]:
        all_session_ids = set(fts_scores.keys()) | set(semantic_scores.keys())

        results = []
        for session_id in all_session_ids:
            fts_score = fts_scores.get(session_id)
            sem_score = semantic_scores.get(session_id)

            if fts_score is not None and sem_score is not None:
                combined = fts_weight * fts_score + semantic_weight * sem_score
            elif fts_score is not None:
                combined = fts_score * 0.5
            elif sem_score is not None:
                combined = sem_score * 0.5
            else:
                continue

            results.append(
                SearchResult(
                    session_id=session_id,
                    score=combined,
                    fts_score=fts_score,
                    semantic_score=sem_score,
                )
            )

        return results

    @staticmethod
    def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
        """Normalize scores to [FLOOR, 1.0]."""
        if not scores:
            return {}

        values = list(scores.values())
        min_val = min(values)
        max_val = max(values)

        if max_val == min_val:
            return {k: 1.0 for k in scores}

        FLOOR = 0.5
        return {k: FLOOR + (1.0 - FLOOR) * (v - min_val) / (max_val - min_val) for k, v in scores.items()}

    def invalidate_cache(self):
        self._embedding_matrix = None
        self._embedding_norms = None
        self._chunk_session_ids = None

    @property
    def has_embeddings(self) -> bool:
        return self._db.count_chunks_with_embeddings() > 0

    @property
    def embeddings_available(self) -> bool:
        return self._embedder.available
