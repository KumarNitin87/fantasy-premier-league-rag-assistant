"""
Document chunking and metadata enrichment for Premier League news and injury updates.
Provides domain-specific chunking, entity recognition, category and severity classification,
and deterministic hash IDs for idempotent vector storage.
"""

import re
import hashlib
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from config.settings import settings


class FootballNewsChunker:
    """Chunks football articles and attaches contextual domain metadata."""

    def __init__(
        self,
        chunk_size: int = settings.CHUNK_SIZE,
        chunk_overlap: int = settings.CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        self.injury_keywords = {
            "injury", "injured", "hamstring", "knee", "knock", "strain", "scan",
            "ruled out", "sidelined", "doubt", "doubtful", "surgery", "fitness test",
            "concussion", "ankle", "groin", "achilles", "calf", "illness", "medical",
            "fracture", "broken", "ligament", "sprain", "physio", "tear",
        }
        self.press_keywords = {
            "press conference", "arteta", "guardiola", "klopp", "slot", "ten hag",
            "postecoglou", "maresca", "iraola", "emery", "howe", "frank", "silva",
            "manager", "boss", "comments", "speaking to", "confirmed", "briefing",
        }
        self.transfer_keywords = {
            "transfer", "signed", "deal", "contract", "fee", "departure", "loan",
            "bid", "agreement", "scout",
        }
        self.tactics_keywords = {
            "tactics", "formation", "rotation", "starting xi", "lineup", "benched",
            "minutes", "role", "system",
        }

        # Severity indicators
        self.out_keywords = {
            "ruled out", "sidelined", "surgery", "out for", "fracture", "broken",
            "acl", "tear", "months out", "weeks out", "miss out", "won't play",
            "will miss", "unavailable", "long-term", "operation",
        }
        self.doubtful_keywords = {
            "doubt", "doubtful", "late fitness test", "knock", "strain", "assessed",
            "minor issue", "50-50", "touch and go", "precaution", "niggle",
            "question mark", "late check", "evaluating",
        }
        self.suspended_keywords = {
            "suspension", "suspended", "red card", "ban", "disciplinary", "yellow card accumulation",
        }
        self.fit_keywords = {
            "fit", "returned to training", "available", "passed fit", "cleared",
            "back in contention", "ready to start", "recovered", "full training",
        }

    def clean_text(self, raw_html_or_text: str) -> str:
        """Strip HTML tags, URLs, and extraneous whitespace."""
        if not raw_html_or_text:
            return ""
        soup = BeautifulSoup(raw_html_or_text, "html.parser")
        text = soup.get_text(separator=" ")
        text = re.sub(r"http\S+|www\.\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def chunk_article(
        self,
        title: str,
        content: str,
        source: str,
        published_at: str,
        link: str = "",
        known_players: Optional[List[str]] = None,
        known_teams: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Split article into metadata-tagged chunks.
        Extracts player, team, category, severity, and timestamp tags.
        """
        clean_title = self.clean_text(title)
        clean_content = self.clean_text(content)
        full_text = f"{clean_title}. {clean_content}" if clean_content else clean_title

        if not full_text:
            return []

        chunks_text = self._create_chunks(full_text)

        category = self._classify_category(full_text)
        severity = self._classify_severity(full_text, category)
        detected_players = self._find_entities(full_text, known_players or [])
        detected_teams = self._find_entities(full_text, known_teams or [])

        primary_player = detected_players[0] if detected_players else "general"
        primary_team = detected_teams[0] if detected_teams else "general"

        result_chunks = []
        for idx, text in enumerate(chunks_text):
            chunk_id = self._generate_id(text, source, clean_title, idx)
            result_chunks.append({
                "id": chunk_id,
                "text": text,
                "metadata": {
                    "title": clean_title[:120],
                    "source": source,
                    "published_at": published_at,
                    "timestamp": published_at,
                    "link": link,
                    "category": category,
                    "severity": severity,
                    "player": primary_player,
                    "player_tags": ", ".join(detected_players) if detected_players else "general",
                    "team": primary_team,
                    "team_tags": ", ".join(detected_teams) if detected_teams else "general",
                }
            })

        return result_chunks

    def _create_chunks(self, text: str) -> List[str]:
        """Split text into overlapping chunks respecting sentence boundaries where possible."""
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        step = max(self.chunk_size - self.chunk_overlap, 100)
        for i in range(0, len(text), step):
            chunk = text[i:i + self.chunk_size]
            if len(chunk.strip()) > 30:
                chunks.append(chunk.strip())

        return chunks

    def _classify_category(self, text: str) -> str:
        """Classify article category based on football domain keyword frequency."""
        lower = text.lower()
        if any(w in lower for w in self.injury_keywords):
            return "injury"
        if any(w in lower for w in self.press_keywords):
            return "press_conference"
        if any(w in lower for w in self.transfer_keywords):
            return "transfer"
        if any(w in lower for w in self.tactics_keywords):
            return "tactics"
        return "general_news"

    def _classify_severity(self, text: str, category: str) -> str:
        """Classify availability severity (out, doubtful, suspended, fit, unknown)."""
        lower = text.lower()
        if any(w in lower for w in self.suspended_keywords):
            return "suspended"
        if any(w in lower for w in self.out_keywords):
            return "out"
        if any(w in lower for w in self.doubtful_keywords):
            return "doubtful"
        if any(w in lower for w in self.fit_keywords):
            return "fit"
        if category == "injury":
            return "doubtful"
        return "unknown"

    @staticmethod
    def _find_entities(text: str, candidates: List[str]) -> List[str]:
        """Detect which known players/teams appear in text using word boundary matching."""
        matched = []
        lower = text.lower()
        for c in candidates:
            c_str = c.strip()
            if len(c_str) >= 3:
                # Use word boundary search to avoid false positive substring matches
                pattern = r"\b" + re.escape(c_str.lower()) + r"\b"
                if re.search(pattern, lower):
                    matched.append(c_str)
        # Deduplicate while preserving order, max 5 entities
        seen = set()
        unique_matches = []
        for m in matched:
            if m.lower() not in seen:
                seen.add(m.lower())
                unique_matches.append(m)
        return unique_matches[:5]

    @staticmethod
    def _generate_id(text: str, source: str, title: str, idx: int) -> str:
        """Generate deterministic MD5 hash ID for idempotent vector store upsert."""
        clean_text_prefix = text[:100].strip().lower()
        seed = f"{source}_{title[:50]}_{idx}_{clean_text_prefix}"
        return hashlib.md5(seed.encode("utf-8")).hexdigest()
