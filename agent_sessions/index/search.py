import math
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .database import SessionDatabase
from .embeddings import EmbeddingGenerator


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
        self._chunk_embeddings_cache: Optional[list[tuple[str, int, bytes]]] = None

    def search(
        self,
        query: str,
        limit: int = 50,
        fts_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
    ) -> list[SearchResult]:
        start_time = time.time()
        fts_w = fts_weight if fts_weight is not None else self._fts_weight
        sem_w = semantic_weight if semantic_weight is not None else self._semantic_weight

        fts_results = self._search_fts(query, limit=limit * 2)
        semantic_results = self._search_semantic(query, limit=limit * 2)

        combined = self._combine_scores(fts_results, semantic_results, fts_w, sem_w)
        combined = [r for r in combined if r.score >= 0.2]
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

        if self._chunk_embeddings_cache is None:
            self._chunk_embeddings_cache = self._db.get_all_chunk_embeddings()

        if not self._chunk_embeddings_cache:
            return {}

        MIN_COSINE = 0.35

        session_scores: dict[str, list[float]] = defaultdict(list)
        for session_id, _chunk_id, embedding_blob in self._chunk_embeddings_cache:
            chunk_embedding = EmbeddingGenerator.deserialize_embedding(embedding_blob)
            similarity = self._cosine_similarity(query_embedding, chunk_embedding)
            session_scores[session_id].append(similarity)

        aggregated: dict[str, float] = {}
        for session_id, similarities in session_scores.items():
            best = max(similarities)
            if best >= MIN_COSINE:
                aggregated[session_id] = best

        if not aggregated:
            return {}

        sorted_sessions = sorted(aggregated.items(), key=lambda x: x[1], reverse=True)
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
        """Normalize scores to [FLOOR, 1.0].

        Uses a floor of 0.5 so even the weakest result in a set retains
        meaningful weight — two perfect FTS matches with similar BM25
        scores shouldn't map to 1.0 and 0.0.
        """
        if not scores:
            return {}

        values = list(scores.values())
        min_val = min(values)
        max_val = max(values)

        if max_val == min_val:
            return {k: 1.0 for k in scores}

        FLOOR = 0.5
        return {k: FLOOR + (1.0 - FLOOR) * (v - min_val) / (max_val - min_val) for k, v in scores.items()}

    @staticmethod
    def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
        if len(vec_a) != len(vec_b):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def invalidate_cache(self):
        self._chunk_embeddings_cache = None

    @property
    def has_embeddings(self) -> bool:
        return self._db.count_chunks_with_embeddings() > 0

    @property
    def embeddings_available(self) -> bool:
        return self._embedder.available
