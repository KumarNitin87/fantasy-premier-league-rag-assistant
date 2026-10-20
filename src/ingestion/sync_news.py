"""
ETL Pipeline for ingesting, chunking, and embedding Premier League news & injury bulletins into ChromaDB.
Combines official FPL medical bulletins with multi-source RSS news feeds.
Provides idempotent execution and resilient error handling.
"""
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional, TYPE_CHECKING
from datetime import datetime, timezone

from config.settings import settings
from src.api.rss_client import RSSNewsClient
from src.database.repository import FPLRepository
from src.rag.chunker import FootballNewsChunker
from src.sync.process_lock import acquire_news_sync_lock

if TYPE_CHECKING:
    from src.rag.vector_store import ChromaVectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("sync_news")


class NewsIngestionPipeline:
    """Orchestrates news collection, entity tagging, embedding generation, and vector indexing."""

    def __init__(
        self,
        rss_client: RSSNewsClient = None,
        chunker: FootballNewsChunker = None,
        vector_store: Optional[ChromaVectorStore] = None,
        repository: FPLRepository = None,
    ):
        self.rss_client = rss_client or RSSNewsClient()
        self.chunker = chunker or FootballNewsChunker()
        if vector_store is None:
            from src.rag.vector_store import ChromaVectorStore
            vector_store = ChromaVectorStore()
        self.vector_store = vector_store
        self.repo = repository or FPLRepository()

    def run(self, max_per_feed: int = 25) -> Dict[str, Any]:
        """
        Execute full news ingestion pipeline.
        Idempotent: Identical articles generate identical chunk IDs and are updated in-place.
        """
        with acquire_news_sync_lock(self.repo.db_path) as acquired:
            if not acquired:
                raise RuntimeError("Another news synchronization is already in progress.")
            return self._run_locked(max_per_feed)

    def _run_locked(self, max_per_feed: int) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = self._run_ingestion(max_per_feed)
        except Exception as exc:
            self._record_sync_status(
                started_at=started_at,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

        errors = result.get("rss_feed_errors", [])
        successful_feeds = result.get("rss_feeds_succeeded", 0)
        status = "partial" if errors and successful_feeds else "error" if errors else "ok"
        error = "; ".join(errors)
        result["status"] = status
        result["error"] = error
        self._record_sync_status(started_at=started_at, status=status, error=error)
        return result

    def _run_ingestion(self, max_per_feed: int) -> Dict[str, Any]:
        """Collect, chunk, embed, and upsert news without changing status logs."""
        logger.info("Starting Premier League news and injury ingestion...")
        start_time = datetime.now(timezone.utc)

        # 1. Fetch active players and teams from SQLite to enable entity tagging
        all_players = self.repo.get_players(limit=750)
        all_teams = self.repo.get_all_teams()

        player_names = [p.web_name for p in all_players] + [f"{p.first_name} {p.second_name}" for p in all_players]
        team_names = [t.name for t in all_teams] + [t.short_name for t in all_teams]

        chunks_to_index: List[Dict[str, Any]] = []

        # 2. Ingest official FPL injury bulletins from database
        injury_players = [p for p in all_players if p.news or p.status != "a"]
        logger.info("Found %d players with official Premier League injury bulletins.", len(injury_players))

        for p in injury_players:
            chance = p.chance_of_playing_next_round if p.chance_of_playing_next_round is not None else (
                0 if p.status in ("i", "s", "u") else 50
            )
            bulletin_text = (
                f"Official Premier League Injury Report: {p.web_name} ({p.team_name or 'Premier League'}). "
                f"Status: {p.status.upper()} ({chance}% chance of playing). "
                f"Manager Report: {p.news or 'Under medical assessment'}."
            )
            chunks = self.chunker.chunk_article(
                title=f"Official Medical Update: {p.web_name} ({p.team_short_name or p.team_name})",
                content=bulletin_text,
                source="Premier League Official Medical Bulletin",
                published_at=p.news_added or "",
                known_players=player_names,
                known_teams=team_names,
            )
            chunks_to_index.extend(chunks)

        # 3. Fetch RSS news articles from configured feeds
        rss_articles = self.rss_client.fetch_all_articles(max_per_feed=max_per_feed)
        rss_feed_errors = list(getattr(self.rss_client, "last_errors", []))
        rss_feeds_succeeded = int(getattr(self.rss_client, "last_successful_feeds", 0))
        logger.info("Fetched %d RSS news articles across configured feeds.", len(rss_articles))

        for article in rss_articles:
            chunks = self.chunker.chunk_article(
                title=article.get("title", ""),
                content=article.get("summary", ""),
                source=article.get("source", "RSS News"),
                published_at=article.get("published_at", ""),
                link=article.get("link", ""),
                known_players=player_names,
                known_teams=team_names,
            )
            chunks_to_index.extend(chunks)

        # 4. Idempotently add chunks to ChromaDB
        indexed_count = self.vector_store.add_chunks(chunks_to_index)
        total_vectors = self.vector_store.count()

        logger.info(
            "News ingestion completed successfully! Chunks processed: %d | Total in collection: %d",
            indexed_count, total_vectors,
        )

        return {
            "indexed_chunks": indexed_count,
            "injury_bulletins": len(injury_players),
            "rss_articles": len(rss_articles),
            "rss_feed_errors": rss_feed_errors,
            "rss_feeds_succeeded": rss_feeds_succeeded,
            "total_vectors": total_vectors,
        }

    def _record_sync_status(
        self,
        started_at: str,
        status: str,
        error: str = "",
    ) -> None:
        try:
            self.repo.log_ingestion(
                sync_type="news",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                status=status,
                error=error,
            )
        except Exception as log_exc:
            logger.warning("Could not record news sync status (%s).", type(log_exc).__name__)


def main():
    """CLI entry point for syncing news."""
    pipeline = NewsIngestionPipeline()
    result = pipeline.run(max_per_feed=settings.MAX_NEWS_ITEMS_PER_FEED)
    print(f"Sync complete: {result}")


if __name__ == "__main__":
    main()
