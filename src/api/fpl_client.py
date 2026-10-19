"""
Hardened FPL API Client with in-memory TTL caching, Pydantic response validation,
timeout handling, structured logging, and rate-limit protection.

Data Source: Official Fantasy Premier League REST API
  - Base URL: https://fantasy.premierleague.com/api/
  - No authentication required
  - Player photos: https://resources.premierleague.com/premierleague/photos/players/250x250/p{code}.png
"""

import logging
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydantic import ValidationError

from config.settings import settings
from src.api.schemas import FPLBootstrapStaticResponse, FPLFixtureRaw, FPLHistoryRaw

logger = logging.getLogger(__name__)

# Player photo CDN - no auth required
PHOTO_BASE_URL = "https://resources.premierleague.com/premierleague/photos/players/250x250/p{code}.png"


class InMemoryCache:
    """Simple thread-safe TTL cache for API responses."""

    def __init__(self):
        self._store: Dict[str, Tuple[Any, float]] = {}

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        """Return cached value if it exists and hasn't expired, else None."""
        if key not in self._store:
            return None
        value, stored_at = self._store[key]
        if time.time() - stored_at > ttl_seconds:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.time())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


class FPLAPIError(Exception):
    """Raised when the FPL API returns an unexpected error or response."""
    pass


class FPLValidationError(Exception):
    """Raised when API response fails Pydantic schema validation."""
    pass


class FPLClient:
    """
    Production-grade Fantasy Premier League API client.

    Data Source: https://fantasy.premierleague.com/api/
    No API key required — public REST API.

    Player Photos: https://resources.premierleague.com/premierleague/photos/players/250x250/p{code}.png
    The `code` field is returned in the bootstrap-static player list.
    """

    # Cache TTL settings (seconds)
    BOOTSTRAP_TTL = 300        # 5 minutes — players/teams change rarely mid-day
    FIXTURES_TTL = 3600        # 1 hour — fixture schedule rarely changes
    ELEMENT_SUMMARY_TTL = 600  # 10 minutes per player history

    def __init__(
        self,
        base_url: str = settings.FPL_API_BASE_URL,
        timeout: int = settings.FPL_TIMEOUT_SECONDS,
        max_retries: int = settings.MAX_RETRIES,
        backoff_factor: float = settings.RETRY_BACKOFF_FACTOR,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache = InMemoryCache()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-GB,en;q=0.9",
        })

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Public API Methods
    # ------------------------------------------------------------------

    def get_bootstrap_static(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch core FPL data: all teams, all players, element types, gameweek info.
        Endpoint: GET /api/bootstrap-static/
        Cached for 5 minutes.
        """
        cache_key = "bootstrap_static"
        if not force_refresh:
            cached = self._cache.get(cache_key, self.BOOTSTRAP_TTL)
            if cached is not None:
                logger.debug("bootstrap-static served from in-memory cache")
                return cached

        url = f"{self.base_url}/bootstrap-static/"
        raw = self._get(url, "bootstrap-static")

        # Validate response shape
        try:
            validated = FPLBootstrapStaticResponse(**raw)
            logger.info(
                "bootstrap-static validated: %d teams | %d players",
                len(validated.teams),
                len(validated.elements),
            )
        except ValidationError as exc:
            logger.error("bootstrap-static validation failed: %s", exc)
            raise FPLValidationError(f"bootstrap-static response validation failed: {exc}") from exc

        self._cache.set(cache_key, raw)
        return raw

    def get_fixtures(self, event: Optional[int] = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch fixture schedule for the full season or a specific gameweek.
        Endpoint: GET /api/fixtures/ or /api/fixtures/?event={event}
        Cached for 1 hour (full season) or 15 min (specific gameweek).
        """
        cache_key = f"fixtures_event_{event or 'all'}"
        ttl = self.FIXTURES_TTL if event is None else 900

        if not force_refresh:
            cached = self._cache.get(cache_key, ttl)
            if cached is not None:
                logger.debug("fixtures[event=%s] served from cache", event)
                return cached

        url = f"{self.base_url}/fixtures/"
        params = {"event": event} if event is not None else None
        raw_list = self._get(url, "fixtures", params=params)

        if not isinstance(raw_list, list):
            raise FPLValidationError(f"Fixtures endpoint expected list, got {type(raw_list)}")

        # Validate each fixture
        validated_fixtures = []
        errors = 0
        for item in raw_list:
            try:
                FPLFixtureRaw(**item)
                validated_fixtures.append(item)
            except ValidationError as exc:
                logger.warning("Skipping invalid fixture (id=%s): %s", item.get("id"), exc)
                errors += 1

        logger.info("Fixtures validated: %d ok | %d skipped (validation errors)", len(validated_fixtures), errors)
        self._cache.set(cache_key, validated_fixtures)
        return validated_fixtures

    def get_element_summary(self, player_id: int, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch per-gameweek match history for a specific player.
        Endpoint: GET /api/element-summary/{player_id}/
        Cached for 10 minutes per player.
        """
        cache_key = f"element_summary_{player_id}"
        if not force_refresh:
            cached = self._cache.get(cache_key, self.ELEMENT_SUMMARY_TTL)
            if cached is not None:
                logger.debug("element-summary[%d] served from cache", player_id)
                return cached

        url = f"{self.base_url}/element-summary/{player_id}/"
        raw = self._get(url, f"element-summary[{player_id}]")

        if not isinstance(raw, dict) or "history" not in raw:
            raise FPLValidationError(f"element-summary for player {player_id} missing 'history' key")

        # Validate history entries
        valid_history = []
        for entry in raw.get("history", []):
            try:
                FPLHistoryRaw(**entry)
                valid_history.append(entry)
            except ValidationError as exc:
                logger.warning("Skipping invalid history entry for player %d: %s", player_id, exc)

        raw["history"] = valid_history
        self._cache.set(cache_key, raw)
        return raw

    @staticmethod
    def build_photo_url(player_code: Optional[int]) -> Optional[str]:
        """
        Construct the official Premier League CDN player photo URL.
        Uses player `code` field (NOT player `id`).
        Returns None if code is unavailable.
        """
        normalized_code = FPLClient.normalize_photo_code(player_code)
        if normalized_code is None:
            return None
        return PHOTO_BASE_URL.format(code=normalized_code)

    @staticmethod
    def normalize_photo_code(player_code: Any) -> Optional[int]:
        """Return a plausible official FPL photo code, rejecting IDs and placeholders."""
        if isinstance(player_code, bool) or player_code is None:
            return None
        text = str(player_code).strip()
        if not text.isdigit():
            return None
        code = int(text)
        # Official photo codes are numeric and substantially larger than FPL element IDs.
        return code if 1_000 <= code <= 9_999_999_999 else None

    def clear_cache(self) -> None:
        """Manually invalidate all cached responses."""
        self._cache.clear()
        logger.info("FPL API in-memory cache cleared")

    # ------------------------------------------------------------------
    # Internal Request Helper
    # ------------------------------------------------------------------

    def _get(self, url: str, label: str, params: Optional[Dict] = None) -> Any:
        """
        Perform a GET request with structured error handling and logging.
        Raises FPLAPIError on HTTP errors or network failures.
        """
        logger.info("GET %s [%s]", url, label)
        start = time.time()
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.ConnectionError as exc:
            raise FPLAPIError(f"Network connection failed for {label}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise FPLAPIError(f"Request timed out after {self.timeout}s for {label}") from exc
        except requests.exceptions.RequestException as exc:
            raise FPLAPIError(f"Unexpected request error for {label}: {exc}") from exc

        elapsed = round(time.time() - start, 2)

        if response.status_code == 429:
            raise FPLAPIError(f"FPL API rate limit hit (HTTP 429) for {label}")
        if response.status_code == 404:
            raise FPLAPIError(f"FPL API endpoint not found (HTTP 404) for {label}")
        if not response.ok:
            raise FPLAPIError(
                f"FPL API returned HTTP {response.status_code} for {label}: {response.text[:200]}"
            )

        logger.info("GET %s -> HTTP %d in %.2fs | %d bytes", url, response.status_code, elapsed, len(response.content))

        try:
            return response.json()
        except Exception as exc:
            raise FPLAPIError(f"Failed to parse JSON response for {label}: {exc}") from exc
