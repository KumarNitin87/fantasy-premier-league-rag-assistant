"""
Hybrid Context Retriever combining ChromaDB vector news search with SQLite structured statistics.
Delivers unified context payloads blending quantitative historical stats with qualitative news/injury intelligence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import re
from src.database.repository import FPLRepository
from src.database.models import Player, Fixture, PlayerGameweekHistory

if TYPE_CHECKING:
    from src.rag.vector_store import ChromaVectorStore


@dataclass
class RetrievalContext:
    """Consolidated context payload for LLM reasoning and UI presentation."""
    query: str
    target_player: Optional[Player] = None
    upcoming_fixtures: List[Fixture] = field(default_factory=list)
    player_history: List[PlayerGameweekHistory] = field(default_factory=list)
    news_chunks: List[Dict[str, Any]] = field(default_factory=list)
    formatted_context: str = ""
    structured_summary: Dict[str, Any] = field(default_factory=dict)


class HybridRetriever:
    """Coordinates semantic unstructured search with exact structured database lookup."""

    def __init__(
        self,
        vector_store: Optional[ChromaVectorStore] = None,
        repository: Optional[FPLRepository] = None,
    ):
        if vector_store is None:
            from src.rag.vector_store import ChromaVectorStore
            vector_store = ChromaVectorStore()
        self.vector_store = vector_store
        self.repo = repository or FPLRepository()

    def retrieve(
        self,
        query: str,
        top_k_news: int = 5,
        category_filter: Optional[str] = None,
        fixture_limit: int = 3,
    ) -> RetrievalContext:
        """Execute hybrid retrieval combining semantic news with structured SQLite data."""
        # 1. Identify player and team mentions in the query
        target_player = self._detect_player(query)

        # 2. Semantic search in ChromaDB
        where_filter = {"category": category_filter} if category_filter else None

        # If a player was identified, run a targeted search for the player's news
        if target_player:
            targeted_query = f"{target_player.web_name} {target_player.team_name or ''} {query}"
            news_chunks = self.vector_store.query(targeted_query, n_results=top_k_news, where_filter=where_filter)
            # If targeted query yielded few results, fallback to general query
            if len(news_chunks) < 2:
                general_chunks = self.vector_store.query(query, n_results=top_k_news, where_filter=where_filter)
                seen_ids = {c["id"] for c in news_chunks}
                for c in general_chunks:
                    if c["id"] not in seen_ids:
                        news_chunks.append(c)
        else:
            news_chunks = self.vector_store.query(query, n_results=top_k_news, where_filter=where_filter)

        # 3. Retrieve structured database information
        upcoming_fixtures: List[Fixture] = []
        player_history: List[PlayerGameweekHistory] = []
        structured_summary: Dict[str, Any] = {}

        if target_player:
            upcoming_fixtures = self.repo.get_upcoming_fixtures(team_id=target_player.team_id, limit=max(1, min(5, fixture_limit)))
            player_history = self.repo.get_player_gameweek_history(player_id=target_player.id, limit=3)
            structured_summary = {
                "player_id": target_player.id,
                "name": target_player.web_name,
                "full_name": target_player.full_name,
                "team": target_player.team_name or target_player.team_short_name,
                "position": target_player.position,
                "cost": target_player.cost_float,
                "form": target_player.form,
                "total_points": target_player.total_points,
                "status": target_player.status,
                "chance_of_playing": target_player.chance_of_playing_next_round or 100,
                "official_news": target_player.news,
                "photo_url": target_player.photo_url,
                "xG": target_player.expected_goals,
                "xA": target_player.expected_assists,
                "ict_index": target_player.ict_index,
            }

        # 4. Format unified context string for LLM or UI
        formatted_context = self._build_prompt_context(
            query=query,
            player=target_player,
            fixtures=upcoming_fixtures,
            history=player_history,
            news_chunks=news_chunks,
        )

        return RetrievalContext(
            query=query,
            target_player=target_player,
            upcoming_fixtures=upcoming_fixtures,
            player_history=player_history,
            news_chunks=news_chunks,
            formatted_context=formatted_context,
            structured_summary=structured_summary,
        )

    def retrieve_for_player(self, player_name: str, top_k_news: int = 5) -> RetrievalContext:
        """Convenience method to retrieve hybrid context for a specific named player."""
        return self.retrieve(query=player_name, top_k_news=top_k_news)

    def _detect_player(self, query: str) -> Optional[Player]:
        """Detect if an active player is referenced in the query using token matching."""
        tokens = [t for t in re.findall(r"\b[A-Za-z]{3,}\b", query) if t.lower() not in {
            "what", "when", "where", "which", "should", "could", "would", "about", "injury",
            "captain", "transfer", "points", "goals", "premier", "league", "fantasy", "team",
            "news", "play", "start", "bench", "cost", "price", "form", "stats", "update",
            "latest", "will", "does", "have", "player", "players", "radar", "feed"
        }]

        # Try multi-word full matches first (e.g. "Erling Haaland", "Bukayo Saka")
        for i in range(len(tokens) - 1):
            pair = f"{tokens[i]} {tokens[i+1]}"
            matches = self.repo.search_players(pair, limit=3)
            if matches:
                return max(matches, key=lambda p: p.total_points)

        # Single word token matching
        for token in tokens:
            matches = self.repo.search_players(token, limit=5)
            if matches:
                # Return the player with highest points
                return max(matches, key=lambda p: p.total_points)

        return None

    def _build_prompt_context(
        self,
        query: str,
        player: Optional[Player],
        fixtures: List[Fixture],
        history: List[PlayerGameweekHistory],
        news_chunks: List[Dict[str, Any]],
    ) -> str:
        """Build clean structured markdown context block."""
        parts = []

        # Structured Player Stats (if identified)
        if player:
            parts.append("### Structured Player Data (Official Premier League Database):")
            parts.append(f"- Name: {player.web_name} ({player.full_name})")
            parts.append(f"- Club: {player.team_name} | Position: {player.position}")
            parts.append(f"- Price: £{player.cost_float}m | Form: {player.form} | Total Points: {player.total_points}")
            parts.append(f"- Goals: {player.goals_scored} | Assists: {player.assists} | Clean Sheets: {player.clean_sheets}")
            parts.append(f"- xG: {player.expected_goals:.2f} | xA: {player.expected_assists:.2f} | ICT Index: {player.ict_index:.1f}")
            chance = player.chance_of_playing_next_round if player.chance_of_playing_next_round is not None else (
                0 if player.status in ("i", "s", "u") else 100
            )
            parts.append(f"- Official Availability Status: {player.status.upper()} (Chance of Playing: {chance}%)")
            if player.news:
                parts.append(f"- Official Medical Bulletin: {player.news} (Updated: {player.news_added or 'Recent'})")

            if fixtures:
                parts.append("- Upcoming Fixtures:")
                for fx in fixtures:
                    is_home = fx.team_h == player.team_id
                    opp_name = fx.team_a_name if is_home else fx.team_h_name
                    loc = "H" if is_home else "A"
                    fdr = fx.team_h_difficulty if is_home else fx.team_a_difficulty
                    parts.append(f"  * GW{fx.event}: vs {opp_name} ({loc}) [FDR: {fdr}]")

            if history:
                parts.append("- Recent Gameweek Performance:")
                for h in history:
                    parts.append(f"  * GW{h.round}: {h.total_points} pts ({h.minutes} mins, {h.goals_scored} goals, {h.assists} assists, {h.bonus} bonus)")

        # News & Press Chunks (from ChromaDB)
        if news_chunks:
            parts.append("\n### Verified Football News & Press Conference Bulletins (Vector Search):")
            for idx, c in enumerate(news_chunks, 1):
                meta = c.get("metadata", {})
                src = meta.get("source", "News")
                pub = str(meta.get("published_at", "Recent"))[:10]
                cat = meta.get("category", "news").upper()
                sev = meta.get("severity", "unknown").upper()
                text = c.get("text", "").strip()
                sim = c.get("similarity", 0.0)
                sim_str = f" [Relevance: {sim*100:.1f}%]" if sim else ""
                parts.append(f"[{idx}] Source: {src} ({pub}) | Category: {cat} | Severity: {sev}{sim_str}: \"{text}\"")

        if not parts:
            parts.append("No specific player or news document matched the query directly.")

        return "\n".join(parts)
