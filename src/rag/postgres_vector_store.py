"""Postgres/pgvector implementation of the retained news and injury index."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config.settings import settings
from src.database.connection import db_session
from src.rag.embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)


class PostgresVectorStore:
    """Persistent cosine-search store backed by the same Postgres database."""

    def __init__(self, embedding_engine: Optional[EmbeddingEngine] = None):
        if not settings.DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required for the hosted vector store.")
        self.embedding_engine = embedding_engine or EmbeddingEngine()
        self._ensure_schema()

    @staticmethod
    def _ensure_schema() -> None:
        with db_session() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS news_vectors (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    embedding vector(384) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );"""
            )

    def add_chunks(self, chunks: List[Dict[str, Any]], batch_size: int = 64) -> int:
        unique = {chunk["id"]: chunk for chunk in chunks if chunk.get("id") and chunk.get("text")}
        total = 0
        for start in range(0, len(unique), batch_size):
            batch = list(unique.values())[start:start + batch_size]
            embeddings = self.embedding_engine.embed_texts([item["text"] for item in batch])
            rows = [
                (item["id"], item["text"], json.dumps(item.get("metadata") or {}), _vector_literal(vector))
                for item, vector in zip(batch, embeddings)
            ]
            with db_session() as conn:
                conn.executemany(
                    """INSERT INTO news_vectors (id, document, metadata, embedding)
                    VALUES (?, ?, ?::jsonb, ?::vector)
                    ON CONFLICT (id) DO UPDATE SET document=EXCLUDED.document,
                        metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding;""",
                    rows,
                )
            total += len(rows)
        return total

    def import_embeddings(self, rows: List[tuple[str, str, Dict[str, Any], List[float]]]) -> int:
        """Import existing Chroma vectors without re-embedding or changing IDs."""
        with db_session() as conn:
            conn.executemany(
                """INSERT INTO news_vectors (id, document, metadata, embedding)
                VALUES (?, ?, ?::jsonb, ?::vector)
                ON CONFLICT (id) DO UPDATE SET document=EXCLUDED.document,
                    metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding;""",
                [
                    (doc_id, document, json.dumps(metadata or {}), _vector_literal(embedding))
                    for doc_id, document, metadata, embedding in rows
                ],
            )
        return len(rows)

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if self.count() == 0:
            return []
        query_embedding = _vector_literal(self.embedding_engine.embed_query(query_text))
        sql = """SELECT id, document AS text, metadata,
            embedding <=> ?::vector AS distance
            FROM news_vectors"""
        params: list[Any] = [query_embedding]
        if where_filter:
            sql += " WHERE metadata @> ?::jsonb"
            params.append(json.dumps(where_filter))
        sql += " ORDER BY embedding <=> ?::vector LIMIT ?"
        params.extend((query_embedding, max(1, n_results)))
        try:
            with db_session() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception:
            logger.exception("Postgres vector query failed.")
            raise
        return [
            {
                "id": row["id"],
                "text": row["text"],
                "metadata": row["metadata"] or {},
                "distance": round(float(row["distance"]), 4),
                "similarity": round(max(0.0, 1.0 - float(row["distance"])), 4),
            }
            for row in rows
        ]

    def count(self) -> int:
        with db_session() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM news_vectors;").fetchone()[0])

    def get_recent(self, limit: int = 12, where_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        sql = "SELECT id, document AS text, metadata FROM news_vectors"
        params: list[Any] = []
        if where_filter:
            sql += " WHERE metadata @> ?::jsonb"
            params.append(json.dumps(where_filter))
        sql += " ORDER BY metadata->>'published_at' DESC NULLS LAST, id LIMIT ?"
        params.append(max(1, limit))
        with db_session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {"id": row["id"], "text": row["text"], "metadata": row["metadata"] or {}, "similarity": None}
            for row in rows
        ]

    def reset(self) -> None:
        with db_session() as conn:
            conn.execute("DELETE FROM news_vectors;")


def _vector_literal(values: List[float]) -> str:
    if len(values) != 384:
        raise ValueError(f"Expected a 384-dimensional embedding, received {len(values)} values.")
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"
