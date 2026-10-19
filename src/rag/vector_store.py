"""
ChromaDB Vector Store integration for indexing and querying football news and injury reports.
Provides persistent vector storage, idempotent upserts, metadata filtering, and semantic similarity search.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from config.settings import settings
from src.rag.embeddings import EmbeddingEngine

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    """Manages persistent ChromaDB vector storage and semantic similarity search."""

    def __init__(
        self,
        persist_dir: Path = settings.CHROMA_PERSIST_DIR,
        collection_name: str = settings.CHROMA_COLLECTION_NAME,
        embedding_engine: Optional[EmbeddingEngine] = None,
    ):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self.embedding_engine = embedding_engine or EmbeddingEngine()

        # Keep import usable for mocked retrieval paths and non-Chroma unit tests.
        # Constructing a real store still imports and exercises ChromaDB.
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: List[Dict[str, Any]], batch_size: int = 64) -> int:
        """
        Embed and upsert chunks idempotently into the ChromaDB collection.
        If chunk IDs already exist, ChromaDB upsert overwrites them without duplicating.
        """
        if not chunks:
            return 0

        # Filter out duplicates within the input batch itself
        unique_chunks_dict: Dict[str, Dict[str, Any]] = {}
        for c in chunks:
            if c.get("id") and c.get("text"):
                unique_chunks_dict[c["id"]] = c
        unique_chunks = list(unique_chunks_dict.values())

        total_upserted = 0
        for i in range(0, len(unique_chunks), batch_size):
            batch = unique_chunks[i:i + batch_size]
            ids = [c["id"] for c in batch]
            texts = [c["text"] for c in batch]
            metadatas = [c["metadata"] for c in batch]

            # Generate dense embeddings using all-MiniLM-L6-v2
            embeddings = self.embedding_engine.embed_texts(texts)

            self.collection.upsert(
                ids=ids,
                documents=texts,
                metadatas=metadatas,
                embeddings=embeddings,
            )
            total_upserted += len(batch)

        logger.info(
            "Idempotently upserted %d document chunks into vector collection '%s' (total in collection: %d)",
            total_upserted, self.collection_name, self.count(),
        )
        return total_upserted

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Perform semantic vector similarity search against the collection."""
        total_count = self.count()
        if total_count == 0:
            logger.info("Vector collection is currently empty.")
            return []

        query_embedding = self.embedding_engine.embed_query(query_text)

        kwargs: Dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(max(1, n_results), total_count),
        }
        if where_filter:
            kwargs["where"] = where_filter

        try:
            results = self.collection.query(**kwargs)
        except Exception as e:
            logger.warning("ChromaDB query error with filter %s: %s; retrying without filter", where_filter, e)
            kwargs.pop("where", None)
            results = self.collection.query(**kwargs)

        formatted = []
        if results and "documents" in results and results["documents"]:
            docs = results["documents"][0]
            metas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(docs)
            dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)
            ids = results["ids"][0] if results.get("ids") else [""] * len(docs)

            for doc_id, doc, meta, dist in zip(ids, docs, metas, dists):
                # Cosine distance to similarity: similarity = 1 - distance
                similarity = max(0.0, 1.0 - dist)
                formatted.append({
                    "id": doc_id,
                    "text": doc,
                    "metadata": meta,
                    "distance": round(float(dist), 4),
                    "similarity": round(float(similarity), 4),
                })

        return formatted

    def count(self) -> int:
        """Return total number of vectors in collection."""
        try:
            return self.collection.count()
        except Exception as err:
            logger.warning("Error fetching collection count: %s", err)
            return 0

    def get_recent(
        self,
        limit: int = 12,
        where_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent indexed documents with optional metadata filtering."""
        if self.count() == 0:
            return []

        try:
            kwargs: Dict[str, Any] = {"limit": limit}
            if where_filter:
                kwargs["where"] = where_filter
            data = self.collection.get(**kwargs)
        except Exception as e:
            logger.warning("ChromaDB get error with filter %s: %s; falling back to peek", where_filter, e)
            data = self.collection.peek(limit=limit)

        results = []
        if data and "documents" in data and data["documents"]:
            docs = data["documents"]
            metas = data.get("metadatas") or [{}] * len(docs)
            ids = data.get("ids") or [""] * len(docs)
            for doc_id, doc, meta in zip(ids, docs, metas):
                results.append({
                    "id": doc_id,
                    "text": doc,
                    "metadata": meta or {},
                    "similarity": None,
                })
        return results

    def reset(self) -> None:
        """Clear all indexed documents."""
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Reset collection '%s'", self.collection_name)
