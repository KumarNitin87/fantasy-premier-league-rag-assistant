"""
Embedding Engine powered by sentence-transformers/all-MiniLM-L6-v2.
Provides fast, lightweight, CPU-optimized semantic vector generation.
"""

import logging
from typing import List, Optional
from config.settings import settings

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    """Manages sentence embedding model lifecycle and inference."""

    _instance: Optional["EmbeddingEngine"] = None
    _model = None

    def __new__(cls, *args, **kwargs):
        """Singleton pattern so heavy weights are loaded into memory exactly once."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, model_name: str = settings.EMBEDDING_MODEL_NAME):
        self.model_name = model_name

    def _ensure_model_loaded(self):
        """Lazy load sentence transformer model."""
        if EmbeddingEngine._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading sentence transformer model '%s'...", self.model_name)
            EmbeddingEngine._model = SentenceTransformer(self.model_name)
            logger.info("Sentence transformer model loaded successfully.")

    def embed_texts(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Compute normalized dense embeddings for a list of strings."""
        if not texts:
            return []
        self._ensure_model_loaded()
        embeddings = EmbeddingEngine._model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embeddings.tolist()

    def embed_query(self, query: str) -> List[float]:
        """Compute normalized embedding for a single search query."""
        self._ensure_model_loaded()
        embedding = EmbeddingEngine._model.encode(
            query,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return embedding.tolist()
