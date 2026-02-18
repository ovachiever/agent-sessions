"""OpenAI embedding generation for semantic search."""

import logging
import os
import struct
from typing import TYPE_CHECKING, Optional, Union

from .chunker import Chunk

logger = logging.getLogger(__name__)

import importlib.util

HAS_OPENAI = importlib.util.find_spec("openai") is not None

if TYPE_CHECKING:
    from openai import OpenAI as OpenAIType

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
BATCH_SIZE = 100


class EmbeddingGenerator:
    """Generates embeddings for session chunks using OpenAI API."""

    def __init__(self):
        self._client: Optional["OpenAIType"] = None
        self._available = False
        self._initialize_client()

    def _initialize_client(self):
        if not HAS_OPENAI:
            logger.debug("OpenAI package not installed - embeddings disabled")
            return

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.debug("OPENAI_API_KEY not set - embeddings disabled")
            return

        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key)
            self._available = True
            logger.debug("OpenAI embeddings initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI client: {e}")

    @property
    def available(self) -> bool:
        return self._available

    @staticmethod
    def serialize_embedding(embedding: list[float]) -> bytes:
        return struct.pack(f'{len(embedding)}f', *embedding)

    @staticmethod
    def deserialize_embedding(blob: bytes) -> list[float]:
        float_count = len(blob) // 4
        return list(struct.unpack(f'{float_count}f', blob))

    def embed_texts(self, texts: list[str]) -> list[Union[list[float], None]]:
        if not self._available or not texts or self._client is None:
            return [None for _ in texts]

        # Truncate texts exceeding embedding model context (8191 tokens; code
        # averages ~3.5 chars/token so use conservative estimate)
        MAX_CHARS = 28000
        texts = [t[:MAX_CHARS] for t in texts]

        try:
            response = self._client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )
            
            embeddings: list[Union[list[float], None]] = [None for _ in texts]
            for item in response.data:
                embeddings[item.index] = item.embedding
            
            return embeddings
            
        except Exception as e:
            logger.error(f"Embedding API error: {e}")
            return [None for _ in texts]

    def embed_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        if not self._available or not chunks:
            return chunks

        MAX_BATCH_TOKENS = 250_000

        # Build token-aware batches
        batches: list[list[Chunk]] = []
        current_batch: list[Chunk] = []
        current_tokens = 0

        for chunk in chunks:
            est_tokens = len(chunk.content) // 4
            if current_batch and current_tokens + est_tokens > MAX_BATCH_TOKENS:
                batches.append(current_batch)
                current_batch = []
                current_tokens = 0
            current_batch.append(chunk)
            current_tokens += est_tokens

        if current_batch:
            batches.append(current_batch)

        for batch in batches:
            # Also respect the count-based limit
            for sub_start in range(0, len(batch), BATCH_SIZE):
                sub_batch = batch[sub_start:sub_start + BATCH_SIZE]
                texts = [c.content for c in sub_batch]

                embeddings = self.embed_texts(texts)

                for c, embedding in zip(sub_batch, embeddings):
                    if embedding is not None:
                        c.embedding = self.serialize_embedding(embedding)

        return chunks

    def embed_query(self, query: str) -> Optional[list[float]]:
        if not self._available or not query or self._client is None:
            return None

        try:
            response = self._client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=[query],
            )
            return response.data[0].embedding
            
        except Exception as e:
            logger.error(f"Query embedding error: {e}")
            return None

    def embed_query_blob(self, query: str) -> Optional[bytes]:
        embedding = self.embed_query(query)
        if embedding is not None:
            return self.serialize_embedding(embedding)
        return None
