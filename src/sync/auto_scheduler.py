"""
FPL and news auto-sync scheduler.

Runs in a background daemon thread while the Streamlit application is active.
Automatically refreshes FPL data at intervals appropriate to the current schedule:
  - Matchday  : every 15 minutes  (when a fixture is today, in progress, or recently provisional)
  - Non-matchday : every 6 hours

Design guarantees:
  - One thread only — re-entrant guard prevents concurrent runs.
  - Thread is a daemon: it terminates automatically when the Python process exits.
    If Streamlit is closed, the thread stops. There is no persistent background scheduler
    that outlives the process — this is deliberate and safe on Windows.
  - Sync failures are logged and recorded in ingestion_log; they never crash the thread.
  - On application start, a staleness check runs immediately so the first page load
    always displays reasonably fresh data without a manual click.
  - After every successful sync the caller is expected to call st.cache_data.clear()
    so Streamlit picks up the new values on the next rerun.

Lifecycle
---------
Call get_scheduler() once (it is @st.cache_resource, so only one instance exists per
Streamlit server process). The scheduler runs FPL polling and due news jobs in its process.
News has an independent interval, lock, and status record.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional, Dict, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Interval constants
# ---------------------------------------------------------------------------
MATCHDAY_INTERVAL_SECONDS   = 15 * 60   # 15 minutes
NON_MATCHDAY_INTERVAL_SECONDS = 6 * 60 * 60   # 6 hours
STARTUP_STALE_THRESHOLD_SECONDS = 30 * 60  # re-sync on startup if data > 30 min old


class FPLAutoScheduler:
    """
    Background daemon thread that calls FPL and optional news callbacks on schedule.

    Parameters
    ----------
    sync_callback:
        A callable() → dict that runs a full FPL sync.  It should return
        {"players": int, "fixtures": int, ...} on success and raise on error.
    matchday_detector:
        A callable() → bool that returns True when there are live or
        recently-played fixtures today (matchday mode).
    last_sync_provider:
        A callable() → str | None that returns the ISO-8601 timestamp of the
        last successful FPL sync, or "Never" / None if unknown.
    on_sync_success:
        Optional callback(result: dict) invoked after a successful sync so
        callers can invalidate caches.
    """

    def __init__(
        self,
        sync_callback: Callable[[], Dict[str, Any]],
        matchday_detector: Callable[[], bool],
        last_sync_provider: Callable[[], Optional[str]],
        on_sync_success: Optional[Callable[[Dict[str, Any]], None]] = None,
        news_sync_callback: Optional[Callable[[], Dict[str, Any]]] = None,
        last_news_sync_provider: Optional[Callable[[], Optional[str]]] = None,
        news_interval_seconds: int = 6 * 60 * 60,
        on_news_sync_success: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._sync_callback = sync_callback
        self._matchday_detector = matchday_detector
        self._last_sync_provider = last_sync_provider
        self._on_sync_success = on_sync_success
        self._news_sync_callback = news_sync_callback
        self._last_news_sync_provider = last_news_sync_provider
        self._news_interval_seconds = news_interval_seconds
        self._on_news_sync_success = on_news_sync_success

        self._lock = threading.Lock()  # re-entrant guard
        self._news_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._immediate_flag = threading.Event()  # set to trigger immediate sync

        # Shared status dict — read by the UI, written by the background thread
        self.status: Dict[str, Any] = {
            "running": False,
            "last_sync_at": None,        # datetime of last successful auto-sync
            "last_error": None,          # str or None
            "next_sync_in": None,        # str human-readable countdown
            "syncing_now": False,
            "mode": "unknown",           # "matchday" or "non-matchday"
            "startup_check_complete": False,
        }
        self.news_status: Dict[str, Any] = {
            "last_attempt_at": None,
            "last_success_at": None,
            "last_partial_at": None,
            "last_error": None,
            "last_result": {},
            "syncing_now": False,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background scheduler thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        last_success = self._safe_last_sync()
        if last_success and last_success != "Never":
            self.status["last_sync_at"] = last_success
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="FPLAutoScheduler",
            daemon=True,  # dies with the process; stops when Streamlit closes
        )
        self._thread.start()
        logger.info("FPLAutoScheduler started (daemon thread).")

    def stop(self) -> None:
        """Signal the scheduler to stop after the current sleep interval."""
        self._stop_event.set()
        self._immediate_flag.set()  # wake from sleep immediately

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the scheduler thread to finish its current sync cycle."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def request_immediate_sync(self) -> None:
        """
        Request an out-of-schedule sync (manual-refresh button).
        The background thread will pick this up as soon as it wakes.
        """
        self._immediate_flag.set()

    def run_news_now(self) -> bool:
        """Run a manual news sync unless another news sync is already active."""
        return self._execute_news_sync(propagate_errors=True)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:
        self.status["running"] = True
        logger.info("FPLAutoScheduler thread running.")

        # ---- startup freshness check ----
        try:
            self._startup_freshness_check()
        except Exception as exc:
            logger.warning("Startup freshness check failed: %s", exc)
        try:
            self._startup_news_freshness_check()
        except Exception as exc:
            logger.warning("News startup freshness check failed: %s", exc)
        finally:
            self.status["startup_check_complete"] = True

        while not self._stop_event.is_set():
            is_matchday = self._safe_matchday_detect()
            interval = MATCHDAY_INTERVAL_SECONDS if is_matchday else NON_MATCHDAY_INTERVAL_SECONDS
            self.status["mode"] = "matchday" if is_matchday else "non-matchday"

            # Sleep in small increments so we respond quickly to stop/immediate events
            deadline = time.monotonic() + interval
            while time.monotonic() < deadline and not self._stop_event.is_set():
                remaining = max(0.0, deadline - time.monotonic())
                self.status["next_sync_in"] = _format_seconds(remaining)
                woken_early = self._immediate_flag.wait(timeout=min(remaining, 30))
                if woken_early or self._immediate_flag.is_set():
                    self._immediate_flag.clear()
                    break
                if self._news_sync_is_due():
                    self._do_news_sync()

            if self._stop_event.is_set():
                break

            self._do_sync()
            if self._news_sync_is_due():
                self._do_news_sync()

        self.status["running"] = False
        logger.info("FPLAutoScheduler thread stopped.")

    def _startup_freshness_check(self) -> None:
        """
        On startup, perform an immediate sync if the last recorded sync is
        missing or older than STARTUP_STALE_THRESHOLD_SECONDS.
        """
        last_sync_raw = self._safe_last_sync()
        if last_sync_raw is None or last_sync_raw in ("Never", ""):
            logger.info("No prior sync recorded — running startup sync.")
            self._do_sync()
            return

        try:
            last_sync_dt = datetime.fromisoformat(last_sync_raw)
            if last_sync_dt.tzinfo is None:
                last_sync_dt = last_sync_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_sync_dt).total_seconds()
            if age > STARTUP_STALE_THRESHOLD_SECONDS:
                logger.info(
                    "Last sync was %.0f min ago (threshold %d min) — running startup sync.",
                    age / 60,
                    STARTUP_STALE_THRESHOLD_SECONDS // 60,
                )
                self._do_sync()
            else:
                logger.info("Data is fresh (%.0f min old) — skipping startup sync.", age / 60)
        except (ValueError, TypeError) as exc:
            logger.warning("Could not parse last_sync timestamp (%s) — running startup sync.", exc)
            self._do_sync()

    def _do_sync(self) -> None:
        """Execute one sync cycle, honouring the re-entrant guard."""
        self._execute_sync(propagate_errors=False)

    def _startup_news_freshness_check(self) -> None:
        if not self._news_sync_callback:
            return
        last_attempt = self._safe_last_news_sync()
        if not last_attempt or last_attempt == "Never" or self._timestamp_age(last_attempt) > self._news_interval_seconds:
            logger.info("News data is missing or stale; running startup news synchronization.")
            self._do_news_sync()

    def _news_sync_is_due(self) -> bool:
        if not self._news_sync_callback:
            return False
        last_attempt = self._safe_last_news_sync()
        return (
            not last_attempt
            or last_attempt == "Never"
            or self._timestamp_age(last_attempt) >= self._news_interval_seconds
        )

    @staticmethod
    def _timestamp_age(timestamp: str) -> float:
        try:
            value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())
        except (TypeError, ValueError):
            return float("inf")

    def _do_news_sync(self) -> None:
        self._execute_news_sync(propagate_errors=False)

    def _execute_news_sync(self, propagate_errors: bool) -> bool:
        if not self._news_sync_callback:
            return False
        if not self._news_lock.acquire(blocking=False):
            logger.info("News sync already in progress — skipping this cycle.")
            return False
        self.news_status["syncing_now"] = True
        self.news_status["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
        try:
            result = self._news_sync_callback()
            self.news_status["last_result"] = result
            result_status = result.get("status", "ok")
            if result_status == "error":
                error = result.get("error") or "No configured news feed completed successfully."
                self.news_status["last_error"] = error
                logger.warning("Scheduled news sync failed: %s", error)
                return False

            if result_status == "partial":
                self.news_status["last_partial_at"] = datetime.now(timezone.utc).isoformat()
                self.news_status["last_error"] = result.get("error")
            else:
                self.news_status["last_success_at"] = datetime.now(timezone.utc).isoformat()
                self.news_status["last_error"] = None
            if self._on_news_sync_success:
                try:
                    self._on_news_sync_success(result)
                    self.news_status["last_callback_error"] = None
                except Exception as callback_error:
                    self.news_status["last_callback_error"] = str(callback_error)
                    logger.exception("on_news_sync_success callback failed.")
            return True
        except Exception as exc:
            self.news_status["last_error"] = str(exc)
            logger.error("News sync cycle failed: %s", exc)
            if propagate_errors:
                raise
            return False
        finally:
            self.news_status["syncing_now"] = False
            self._news_lock.release()

    def run_sync_now(self) -> bool:
        """Run a manual sync immediately unless another sync is already active."""
        return self._execute_sync(propagate_errors=True)

    def _execute_sync(self, propagate_errors: bool) -> bool:
        if not self._lock.acquire(blocking=False):
            logger.info("Sync already in progress — skipping this cycle.")
            return False
        self.status["syncing_now"] = True
        self.status["last_attempt_at"] = datetime.now(timezone.utc).isoformat()
        try:
            logger.info("FPLAutoScheduler: starting sync cycle (%s mode).", self.status["mode"])
            result = self._sync_callback()
            self.status["last_sync_at"] = datetime.now(timezone.utc).isoformat()
            self.status["last_error"] = None
            self.status["last_result"] = result
            logger.info(
                "FPLAutoScheduler: sync complete — players=%s fixtures=%s history=%s",
                result.get("players"), result.get("fixtures"), result.get("history_records"),
            )
            if self._on_sync_success:
                try:
                    self._on_sync_success(result)
                    self.status["last_callback_error"] = None
                except Exception as cb_exc:
                    self.status["last_callback_error"] = str(cb_exc)
                    logger.exception("on_sync_success callback failed.")
            return True
        except Exception as exc:
            self.status["last_error"] = str(exc)
            logger.error("FPLAutoScheduler: sync cycle failed: %s", exc)
            if propagate_errors:
                raise
            return False
        finally:
            self.status["syncing_now"] = False
            self._lock.release()

    # ------------------------------------------------------------------
    # Safe wrappers (never propagate exceptions into the scheduler loop)
    # ------------------------------------------------------------------

    def _safe_matchday_detect(self) -> bool:
        try:
            return bool(self._matchday_detector())
        except Exception as exc:
            logger.warning("matchday_detector raised: %s — defaulting non-matchday.", exc)
            return False

    def _safe_last_sync(self) -> Optional[str]:
        try:
            return self._last_sync_provider()
        except Exception as exc:
            logger.warning("last_sync_provider raised: %s", exc)
            return None

    def _safe_last_news_sync(self) -> Optional[str]:
        if not self._last_news_sync_provider:
            return self.news_status.get("last_attempt_at")
        try:
            return self._last_news_sync_provider()
        except Exception as exc:
            logger.warning("last_news_sync_provider raised: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_seconds(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def detect_matchday(repo) -> bool:
    """
    Return True when there is at least one fixture either:
      - Any fixture currently marked started and not finished, or
      - Scheduled for today's UTC date, or
      - Marked finished_provisional and kicked off within the last 24 hours
    This triggers the 15-minute polling cadence.
    """
    try:
        today = datetime.now(timezone.utc).date()
        # Poll for fixtures today, any currently started match, or provisional points.
        with __import__("src.database.connection", fromlist=["db_session"]).db_session(repo.db_path) as conn:
            rows = conn.execute(
                """
                SELECT COUNT(*) FROM fixtures
                WHERE
                    (date(kickoff_time) = ? AND kickoff_time IS NOT NULL)
                    OR (started = 1 AND finished = 0)
                    OR (
                        finished_provisional = 1
                        AND kickoff_time IS NOT NULL
                        AND julianday(kickoff_time) >= julianday('now', '-24 hours')
                    )
                """,
                (str(today),),
            ).fetchone()
        return (rows[0] or 0) > 0
    except Exception as exc:
        logger.warning("detect_matchday query failed: %s", exc)
        return False
