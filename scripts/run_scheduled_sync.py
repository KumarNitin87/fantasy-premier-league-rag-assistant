"""Run one due FPL or news sync from a hosted scheduler, then exit."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.database.connection import init_db
from src.database.diagnostics import database_failure_category
from src.database.repository import FPLRepository
from src.sync.auto_scheduler import detect_matchday, MATCHDAY_INTERVAL_SECONDS, NON_MATCHDAY_INTERVAL_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scheduled_sync")


def _run_stage(stage: str, action):
    try:
        return action()
    except Exception as exc:
        logger.error(
            "Scheduled sync stage failed (stage=%s category=%s).",
            stage, database_failure_category(exc),
        )
        raise


def _is_due(last_attempt: str | None, interval: int) -> bool:
    if not last_attempt or last_attempt == "Never":
        return True
    try:
        value = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - value).total_seconds() >= interval
    except (TypeError, ValueError):
        return True


def run_once(sync_type: str) -> str:
    if not settings.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for scheduled cloud sync.")
    _run_stage("database_initialization", lambda: init_db(settings.SQLITE_DB_PATH))
    repository = _run_stage("repository_construction", FPLRepository)
    status = _run_stage("load_sync_status", repository.get_db_stats)

    if sync_type in ("fpl", "all"):
        matchday = _run_stage("matchday_detection", lambda: detect_matchday(repository))
        interval = MATCHDAY_INTERVAL_SECONDS if matchday else NON_MATCHDAY_INTERVAL_SECONDS
        if _is_due(status.get("last_sync"), interval):
            from src.ingestion.sync_fpl import FPLSyncPipeline
            result = _run_stage(
                "fpl_ingestion",
                lambda: FPLSyncPipeline(repository=repository).sync_all(
                    sync_player_history_top_n=30,
                    force_refresh=True,
                    allow_fallback=False,
                ),
            )
            logger.info("FPL sync completed: players=%s fixtures=%s histories=%s", result.get("players"), result.get("fixtures"), result.get("history_records"))
        else:
            logger.info("FPL data is still within its %s-second refresh interval; skipped.", interval)

    if sync_type in ("news", "all"):
        # Attempt time is intentionally used to avoid hot-looping a failed feed.
        # The source error is persisted by the ingestion pipeline and retried later.
        latest_status = _run_stage("load_news_sync_status", repository.get_db_stats)
        if _is_due(latest_status.get("last_news_attempt_at"), settings.NEWS_REFRESH_INTERVAL_HOURS * 3600):
            from src.ingestion.sync_news import NewsIngestionPipeline
            result = _run_stage(
                "news_ingestion",
                lambda: NewsIngestionPipeline(repository=repository).run(max_per_feed=settings.MAX_NEWS_ITEMS_PER_FEED),
            )
            if result.get("status") == "error":
                raise RuntimeError("News sync had no successful feed; prior saved evidence is preserved.")
            logger.info("News sync completed with status=%s chunks=%s", result.get("status"), result.get("indexed_chunks"))
        else:
            logger.info("News data is still within its %s-hour refresh interval; skipped.", settings.NEWS_REFRESH_INTERVAL_HOURS)
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=("fpl", "news", "all"), required=True)
    args = parser.parse_args()
    try:
        run_once(args.type)
        return 0
    except Exception as exc:
        # Do not log raw exceptions: database drivers can include connection details.
        logger.error("Scheduled sync failed (category=%s).", database_failure_category(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
