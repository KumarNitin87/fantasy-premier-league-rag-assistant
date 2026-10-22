"""
RSS and News feed parser for Premier League news, team updates, and injuries.
Provides resilient multi-source fetching, custom headers, timeout handling, and deduplication.
"""

import logging
import hashlib
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.constants import DEFAULT_RSS_FEEDS

logger = logging.getLogger(__name__)


class RSSNewsClient:
    """Client for fetching and parsing football news and injury RSS feeds with resilience."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 PL-Fantasy-Predictor/1.0"
    )

    def __init__(
        self,
        feeds: Optional[Dict[str, str]] = None,
        timeout: int = 10,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.feeds = feeds or DEFAULT_RSS_FEEDS
        self.timeout = timeout
        self.user_agent = user_agent
        self.last_errors: List[str] = []
        self.last_attempted_feeds = 0
        self.last_successful_feeds = 0
        self._last_fetch_error: Optional[str] = None

        # Set up resilient requests session
        self.session = requests.Session()
        retries = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": self.user_agent})

    def fetch_all_articles(self, max_per_feed: int = 25) -> List[Dict[str, Any]]:
        """
        Fetch articles across all configured RSS feeds.
        Continues processing remaining feeds if one fails.
        Deduplicates by URL and normalized title hash.
        """
        all_articles: List[Dict[str, Any]] = []
        seen_fingerprints: Set[str] = set()
        self.last_errors = []
        self.last_attempted_feeds = len(self.feeds)
        self.last_successful_feeds = 0

        for source_name, feed_url in self.feeds.items():
            try:
                # Feed URLs may carry access parameters; keep logs to source labels.
                logger.info("Fetching RSS feed: '%s'", source_name)
                self._last_fetch_error = None
                raw_xml = self._fetch_feed_content(feed_url)

                if not raw_xml:
                    error = self._last_fetch_error or "Empty feed response"
                    self.last_errors.append(f"{source_name}: {error}")
                    logger.warning("Feed '%s' could not be fetched: %s", source_name, error)
                    continue
                feed = feedparser.parse(raw_xml)

                if getattr(feed, "bozo", False) and not feed.entries:
                    error = getattr(feed, "bozo_exception", "Invalid or unavailable feed response")
                    self.last_errors.append(f"{source_name}: {type(error).__name__}")
                    logger.warning("Feed '%s' returned invalid XML or error (%s).", source_name, type(error).__name__)
                    continue

                self.last_successful_feeds += 1
                entries = feed.entries[:max_per_feed]
                valid_count = 0
                for entry in entries:
                    article = self._normalize_entry(entry, source_name)
                    fingerprint = self._compute_fingerprint(article)
                    if fingerprint not in seen_fingerprints and article["title"]:
                        seen_fingerprints.add(fingerprint)
                        all_articles.append(article)
                        valid_count += 1

                logger.info("Successfully parsed %d unique articles from '%s'", valid_count, source_name)

            except Exception as e:
                self.last_errors.append(f"{source_name}: {type(e).__name__}")
                logger.warning("Failed to fetch RSS from '%s' (%s); skipping and continuing.", source_name, type(e).__name__)

        return all_articles

    def _fetch_feed_content(self, url: str) -> Optional[str]:
        """Fetch raw XML content with timeout and user-agent."""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            self._last_fetch_error = f"HTTP {resp.status_code}"
            logger.debug("HTTP %s when fetching a feed.", resp.status_code)
        except Exception as err:
            self._last_fetch_error = type(err).__name__
            logger.debug("Direct feed request failed (%s).", type(err).__name__)
        return None

    @staticmethod
    def _compute_fingerprint(article: Dict[str, Any]) -> str:
        """Create a unique hash based on link or title."""
        link = article.get("link", "").strip()
        if link:
            return hashlib.md5(link.encode("utf-8")).hexdigest()
        title = article.get("title", "").strip().lower()
        return hashlib.md5(title.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_entry(entry: Any, source_name: str) -> Dict[str, Any]:
        """Normalize feedparser entry into standard dictionary structure."""
        title = getattr(entry, "title", "").strip()
        summary = getattr(entry, "summary", "").strip()
        link = getattr(entry, "link", "")
        published = getattr(entry, "published", "") or getattr(entry, "updated", "")

        # Keep an unknown source date unknown; the enrichment layer must not invent one.
        published_iso = published.strip() if isinstance(published, str) else ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                published_iso = datetime(*entry.published_parsed[:6]).isoformat()
            except Exception:
                pass

        return {
            "title": title,
            "summary": summary,
            "link": link,
            "source": source_name,
            "published_at": published_iso,
        }
