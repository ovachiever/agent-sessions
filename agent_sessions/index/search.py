import re
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..search import parse_date_value
from .database import SessionDatabase
from .embeddings import EmbeddingGenerator, EMBEDDING_DIMENSIONS


@dataclass
class SearchResult:
    session_id: str
    score: float
    fts_score: Optional[float] = None
    semantic_score: Optional[float] = None
    match_snippet: Optional[str] = None
    match_source: Optional[str] = None


@dataclass
class ParsedHybridQuery:
    text: str
    tag_filters: list[str]
    harness: Optional[str] = None
    project: Optional[str] = None
    after_ts: Optional[int] = None
    before_ts: Optional[int] = None

    @property
    def has_filters(self) -> bool:
        return any(
            [
                self.tag_filters,
                self.harness,
                self.project,
                self.after_ts is not None,
                self.before_ts is not None,
            ]
        )


@dataclass
class _ScoredMatch:
    score: float
    snippet: Optional[str] = None
    source: Optional[str] = None


_MODIFIER_RE = re.compile(
    r"(?<!\S)(harness|project|after|before):(\"[^\"]+\"|'[^']+'|\S+)",
    re.IGNORECASE,
)

_NATURAL_QUERY_PATTERNS = [
    re.compile(
        r"^(?:please\s+)?(?:find|show|list|get|pull\s+up|look\s+up|look\s+for|search\s+for)\s+"
        r"(?:me\s+)?(?:the\s+)?(?:sessions?|conversations?|chats?|threads?)\s+"
        r"(?:where\s+)?(?:we\s+)?(?:were\s+)?"
        r"(?:worked|work|working|talked|discussed|built|fixed|debugged|implemented|created|added|changed)\s+"
        r"(?:on|about|with|for)?\s+(?P<topic>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:which|what)\s+(?:sessions?|conversations?|chats?|threads?)\s+"
        r"(?:did\s+)?(?:we\s+)?"
        r"(?:worked|work|working|talk|talked|discuss|discussed|build|built|fix|fixed|debug|debugged|implement|implemented)\s+"
        r"(?:on|about|with|for)?\s+(?P<topic>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:sessions?|conversations?|chats?|threads?)\s+"
        r"(?:where\s+)?(?:we\s+)?(?:were\s+)?"
        r"(?:worked|work|working|talked|discussed|built|fixed|debugged|implemented|created|added|changed)\s+"
        r"(?:on|about|with|for)?\s+(?P<topic>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:where\s+)?(?:we\s+)?(?:were\s+)?"
        r"(?:worked|work|working|talked|discussed|built|fixed|debugged|implemented|created|added|changed)\s+"
        r"(?:on|about|with|for)\s+(?P<topic>.+)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:find|show|list|get|pull\s+up|look\s+up|look\s+for|search\s+for)\s+"
        r"(?:me\s+)?(?:the\s+)?(?:sessions?|conversations?|chats?|threads?)\s+"
        r"(?:about|on|for|with)\s+(?P<topic>.+)$",
        re.IGNORECASE,
    ),
]


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _normalize_natural_language_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""

    cleaned = cleaned.strip("\"'").strip()
    cleaned = re.sub(r"\bplease\b[.?!]*$", "", cleaned, flags=re.IGNORECASE).strip()

    for pattern in _NATURAL_QUERY_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            topic = match.group("topic").strip(" \"'")
            if topic:
                return topic

    cleaned = re.sub(
        r"^(?:please\s+)?(?:find|show|list|get|pull\s+up|look\s+up|look\s+for|search\s+for)\s+(?:me\s+)?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^(?:the\s+)?(?:sessions?|conversations?|chats?|threads?)\s+(?:about|on|for|with)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned


def parse_hybrid_query(query: str) -> ParsedHybridQuery:
    """Parse inline filters and normalize natural-language search phrasing."""

    tag_filters = re.findall(r"#tag:(\S+)", query)
    remaining = re.sub(r"#tag:\S+", " ", query)

    filters: dict[str, str] = {}

    def remove_modifier(match: re.Match) -> str:
        key = match.group(1).lower()
        filters[key] = _strip_quotes(match.group(2))
        return " "

    remaining = _MODIFIER_RE.sub(remove_modifier, remaining)

    after_ts = None
    if filters.get("after"):
        after_dt = parse_date_value(filters["after"])
        if after_dt is not None:
            after_ts = int(after_dt.timestamp())

    before_ts = None
    if filters.get("before"):
        before_dt = parse_date_value(filters["before"])
        if before_dt is not None:
            before_ts = int(before_dt.timestamp())

    return ParsedHybridQuery(
        text=_normalize_natural_language_text(remaining),
        tag_filters=tag_filters,
        harness=filters.get("harness"),
        project=filters.get("project"),
        after_ts=after_ts,
        before_ts=before_ts,
    )


def _clean_snippet(text: Optional[str], max_chars: int = 240) -> Optional[str]:
    if not text:
        return None
    snippet = re.sub(r"\s+", " ", text).strip()
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 3].rstrip() + "..."
    return snippet or None


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
        self._chunk_ids: Optional[list[int]] = None

    def _load_embedding_cache(self):
        """Load embeddings from DB into a pre-normalized numpy matrix."""
        raw = self._db.get_all_chunk_embeddings()
        if not raw:
            self._embedding_matrix = np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
            self._embedding_norms = np.empty(0, dtype=np.float32)
            self._chunk_session_ids = []
            self._chunk_ids = []
            return

        session_ids = []
        chunk_ids = []
        # Deserialize all blobs into a contiguous float32 array
        flat = bytearray()
        for session_id, chunk_id, blob in raw:
            session_ids.append(session_id)
            chunk_ids.append(chunk_id)
            flat.extend(blob)

        n = len(session_ids)
        matrix = np.frombuffer(bytes(flat), dtype=np.float32).reshape(n, -1)

        # Pre-compute norms for cosine similarity
        norms = np.linalg.norm(matrix, axis=1)

        self._embedding_matrix = matrix
        self._embedding_norms = norms
        self._chunk_session_ids = session_ids
        self._chunk_ids = chunk_ids

    def search(
        self,
        query: str,
        limit: int = 50,
        fts_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
        harness: Optional[str] = None,
        project: Optional[str] = None,
    ) -> list[SearchResult]:
        start_time = time.time()

        parsed = parse_hybrid_query(query)
        if harness:
            parsed.harness = harness
        if project:
            parsed.project = project

        candidate_session_ids: Optional[list[str]] = None
        candidate_session_id_set: Optional[set[str]] = None
        if parsed.has_filters:
            candidate_session_ids = self._db.find_session_ids(
                harness=parsed.harness,
                project=parsed.project,
                after_ts=parsed.after_ts,
                before_ts=parsed.before_ts,
                tag_filters=parsed.tag_filters,
            )
            if not candidate_session_ids:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self._db.log_semantic_search(query, 0, [], elapsed_ms)
                return []
            candidate_session_id_set = set(candidate_session_ids)

        # If query is only filters, return all matching sessions by recency.
        if not parsed.text:
            if candidate_session_ids is None:
                return []
            results = [
                SearchResult(session_id=sid, score=1.0)
                for sid in candidate_session_ids[:limit]
            ]
            elapsed_ms = int((time.time() - start_time) * 1000)
            top_ids = [r.session_id for r in results[:10]]
            self._db.log_semantic_search(query, len(results), top_ids, elapsed_ms)
            return results

        fts_w = fts_weight if fts_weight is not None else self._fts_weight
        sem_w = semantic_weight if semantic_weight is not None else self._semantic_weight

        fts_results = self._search_fts(parsed, limit=limit * 2)
        semantic_results = self._search_semantic(
            parsed.text,
            limit=limit * 2,
            candidate_session_ids=candidate_session_id_set,
        )

        combined = self._combine_scores(fts_results, semantic_results, fts_w, sem_w)
        combined = [r for r in combined if r.score >= 0.2]

        if candidate_session_id_set is not None:
            combined = [r for r in combined if r.session_id in candidate_session_id_set]

        combined.sort(key=lambda r: r.score, reverse=True)
        results = combined[:limit]

        elapsed_ms = int((time.time() - start_time) * 1000)
        top_ids = [r.session_id for r in results[:10]]
        self._db.log_semantic_search(query, len(results), top_ids, elapsed_ms)

        return results

    def search_fts_only(self, query: str, limit: int = 50) -> list[SearchResult]:
        fts_results = self._search_fts(parse_hybrid_query(query), limit=limit)
        return [
            SearchResult(
                session_id=sid,
                score=match.score,
                fts_score=match.score,
                match_snippet=match.snippet,
                match_source=match.source,
            )
            for sid, match in fts_results.items()
        ]

    def search_semantic_only(self, query: str, limit: int = 50) -> list[SearchResult]:
        semantic_results = self._search_semantic(query, limit=limit)
        return [
            SearchResult(
                session_id=sid,
                score=match.score,
                semantic_score=match.score,
                match_snippet=match.snippet,
                match_source=match.source,
            )
            for sid, match in semantic_results.items()
        ]

    def _search_fts(self, parsed: ParsedHybridQuery, limit: int) -> dict[str, _ScoredMatch]:
        msg_results = self._db.search_messages_fts(
            parsed.text,
            limit=limit,
            harness=parsed.harness,
            project=parsed.project,
            after_ts=parsed.after_ts,
            before_ts=parsed.before_ts,
            tag_filters=parsed.tag_filters,
        )
        sess_results = self._db.search_sessions_fts(
            parsed.text,
            limit=limit,
            harness=parsed.harness,
            project=parsed.project,
            after_ts=parsed.after_ts,
            before_ts=parsed.before_ts,
            tag_filters=parsed.tag_filters,
        )

        matches: dict[str, _ScoredMatch] = {}
        for session_id, bm25_score, snippet in msg_results:
            matches[session_id] = _ScoredMatch(
                score=-bm25_score,
                snippet=_clean_snippet(snippet),
                source="keyword",
            )
        for session_id, bm25_score, snippet in sess_results:
            score = -bm25_score
            if session_id not in matches or score > matches[session_id].score:
                matches[session_id] = _ScoredMatch(
                    score=score,
                    snippet=_clean_snippet(snippet),
                    source="metadata",
                )

        return self._normalize_matches(matches)

    def _search_semantic(
        self,
        query: str,
        limit: int,
        candidate_session_ids: Optional[set[str]] = None,
    ) -> dict[str, _ScoredMatch]:
        query_embedding = self._embedder.embed_query(query)
        if query_embedding is None:
            return {}

        if self._embedding_matrix is None:
            self._load_embedding_cache()

        matrix = self._embedding_matrix
        norms = self._embedding_norms
        session_ids = self._chunk_session_ids
        chunk_ids = self._chunk_ids

        if (
            matrix is None
            or norms is None
            or session_ids is None
            or chunk_ids is None
            or len(session_ids) == 0
        ):
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
        session_best_chunk: dict[str, int] = {}
        for i, sim in enumerate(similarities):
            sid = session_ids[i]
            if candidate_session_ids is not None and sid not in candidate_session_ids:
                continue
            if sim >= MIN_COSINE:
                if sid not in session_best or sim > session_best[sid]:
                    session_best[sid] = float(sim)
                    session_best_chunk[sid] = chunk_ids[i]

        if not session_best:
            return {}

        sorted_sessions = sorted(session_best.items(), key=lambda x: x[1], reverse=True)
        top_session_ids = [sid for sid, _ in sorted_sessions[:limit]]
        top_chunk_ids = [session_best_chunk[sid] for sid in top_session_ids]
        chunks_by_id = self._db.get_chunks_by_ids(top_chunk_ids)

        matches: dict[str, _ScoredMatch] = {}
        for sid in top_session_ids:
            chunk = chunks_by_id.get(session_best_chunk[sid])
            matches[sid] = _ScoredMatch(
                score=session_best[sid],
                snippet=_clean_snippet(chunk.content if chunk else None),
                source="semantic",
            )

        return self._normalize_matches(matches)

    def _combine_scores(
        self,
        fts_scores: dict[str, _ScoredMatch],
        semantic_scores: dict[str, _ScoredMatch],
        fts_weight: float,
        semantic_weight: float,
    ) -> list[SearchResult]:
        all_session_ids = set(fts_scores.keys()) | set(semantic_scores.keys())

        results = []
        for session_id in all_session_ids:
            fts_match = fts_scores.get(session_id)
            sem_match = semantic_scores.get(session_id)
            fts_score = fts_match.score if fts_match else None
            sem_score = sem_match.score if sem_match else None

            if fts_score is not None and sem_score is not None:
                combined = fts_weight * fts_score + semantic_weight * sem_score
                best_match = sem_match if sem_score >= fts_score else fts_match
            elif fts_score is not None:
                combined = fts_score * 0.5
                best_match = fts_match
            elif sem_score is not None:
                combined = sem_score * 0.5
                best_match = sem_match
            else:
                continue

            results.append(
                SearchResult(
                    session_id=session_id,
                    score=combined,
                    fts_score=fts_score,
                    semantic_score=sem_score,
                    match_snippet=best_match.snippet if best_match else None,
                    match_source=best_match.source if best_match else None,
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

    def _normalize_matches(self, matches: dict[str, _ScoredMatch]) -> dict[str, _ScoredMatch]:
        normalized = self._normalize_scores({k: v.score for k, v in matches.items()})
        return {
            session_id: _ScoredMatch(
                score=normalized[session_id],
                snippet=match.snippet,
                source=match.source,
            )
            for session_id, match in matches.items()
        }

    def invalidate_cache(self):
        self._embedding_matrix = None
        self._embedding_norms = None
        self._chunk_session_ids = None
        self._chunk_ids = None

    @property
    def has_embeddings(self) -> bool:
        return self._db.count_chunks_with_embeddings() > 0

    @property
    def embeddings_available(self) -> bool:
        return self._embedder.available
