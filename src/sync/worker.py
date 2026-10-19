"""Standalone FPL scheduler process for persistent Docker Compose deployments."""

import logging
import signal
import threading
import time
from datetime import datetime, timezone

from config.settings import settings
from src.database.connection import init_db
from src.database.repository import FPLRepository
from src.ingestion.sync_fpl import FPLSyncPipeline
from src.sync.auto_scheduler import FPLAutoScheduler, detect_matchday

logger = logging.getLogger("fpl_sync_worker")
HEARTBEAT_INTERVAL_SECONDS = 15


def main() -> None:
    """Run scheduled FPL synchronization independently of Streamlit requests."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    init_db(settings.SQLITE_DB_PATH)
    repository = FPLRepository()
    heartbeat = settings.SQLITE_DB_PATH.parent / ".fpl_scheduler_heartbeat"
    heartbeat.unlink(missing_ok=True)
    stopped = threading.Event()

    def request_stop(_signum, _frame):
        stopped.set()
        scheduler.stop()

    def sync_fpl():
        return FPLSyncPipeline(repository=repository).sync_all(
            sync_player_history_top_n=30,
            force_refresh=True,
            allow_fallback=False,
        )

    scheduler = FPLAutoScheduler(
        sync_callback=sync_fpl,
        matchday_detector=lambda: detect_matchday(repository),
        last_sync_provider=lambda: repository.get_db_stats().get("last_sync"),
    )

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    scheduler.start()
    logger.info("Standalone FPL scheduler worker started.")

    while not stopped.is_set():
        if not scheduler.is_alive():
            logger.error("Scheduler thread stopped unexpectedly; terminating for container restart.")
            raise RuntimeError("FPL scheduler thread stopped unexpectedly.")
        if scheduler.status.get("startup_check_complete"):
            try:
                heartbeat.write_text(
                    datetime.now(timezone.utc).isoformat(),
                    encoding="utf-8",
                )
            except OSError:
                logger.exception("Could not update scheduler heartbeat at %s.", heartbeat)
        stopped.wait(HEARTBEAT_INTERVAL_SECONDS)

    scheduler.join(timeout=90)
    if scheduler.is_alive():
        logger.error("Scheduler did not stop within 90 seconds; a sync may still be active.")
    logger.info("Standalone FPL scheduler worker stopped.")


if __name__ == "__main__":
    main()
