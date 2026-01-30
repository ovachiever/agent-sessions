"""SQLite index for fast session search and retrieval."""

from .chunker import Chunk, SessionChunker
from .database import ChunkRow, MessageRow, SessionDatabase, SessionRow
from .embeddings import EMBEDDING_DIMENSIONS, EmbeddingGenerator
from .indexer import SessionIndexer
from .tagger import AutoTagger

__all__ = [
    "SessionDatabase",
    "SessionRow",
    "MessageRow",
    "ChunkRow",
    "SessionIndexer",
    "AutoTagger",
    "SessionChunker",
    "Chunk",
    "EmbeddingGenerator",
    "EMBEDDING_DIMENSIONS",
]
