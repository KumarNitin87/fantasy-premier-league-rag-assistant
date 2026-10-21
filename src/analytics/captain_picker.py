"""
Captaincy Recommendation Engine: Multi-criteria decision analysis (MCDA) for captain picks.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Callable
from src.database.models import Player, Fixture
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine, ScoreBreakdown


@dataclass
class CaptainSuggestion:
    """Ranked captain suggestion with rationale."""
    rank: int
    player: Player
    captain_index: float  # 0 to 100
    tier_label: str  # "Primary Captain Pick", "Safe Vice-Captain", "Differential Captain"
    badge_color: str
    next_opponent: str
    is_home: bool
    fdr: int
    score_breakdown: ScoreBreakdown
    rationale: str


class CaptainPicker:
    """Evaluates and ranks top captaincy options for the upcoming gameweek."""

    def __init__(
        self,
        repository: Optional[FPLRepository] = None,
        scoring_engine: Optional[DecisionScoringEngine] = None,
    ):
        self.repo = repository or FPLRepository()
        self.scoring = scoring_engine or DecisionScoringEngine(
            team_provider=self.repo.get_all_teams
        )

    def get_top_captains(
        self,
        top_n: int = 5,
        negative_news_player_ids: Optional[set] = None,
        candidate_players: Optional[List[Player]] = None,
        fixtures_provider: Optional[Callable[[int, int], List[Fixture]]] = None,
    ) -> List[CaptainSuggestion]:
        """Rank and return top captain candidates."""
        # 1. Fetch top attacking candidates (MID & FWD, plus high scoring players)
        candidates = candidate_players if candidate_players is not None else self.repo.get_players(limit=60, sort_by="total_points")
        # Filter only active players with at least some form
        eligible = [p for p in candidates if p.status in {"a", "d"} and p.minutes > 90 and p.position in {"MID", "FWD"}]

        scored_candidates = []
        for player in eligible:
            fxs = (
                fixtures_provider(player.team_id, 3)
                if fixtures_provider
                else self.repo.get_upcoming_fixtures(team_id=player.team_id, limit=3)
            )
            if not fxs:
                continue

            next_fx = fxs[0]
            is_home = next_fx.team_h == player.team_id
            opponent_name = next_fx.team_a_short_name if is_home else next_fx.team_h_short_name
            fdr = next_fx.team_h_difficulty if is_home else next_fx.team_a_difficulty

            breakdown = self.scoring.evaluate_player(
                player, fxs,
                has_negative_news=bool(negative_news_player_ids and player.id in negative_news_player_ids),
            )

            # Captain-specific adjustments:
            # Home multiplier: 1.10x for home matches
            home_bonus = 1.08 if is_home else 0.95
            # FDR difficulty discount
            fdr_factor = (6.0 - fdr) / 4.0  # FDR 2 -> 1.0, FDR 3 -> 0.75, FDR 4 -> 0.5
            # Explosiveness factor: goals + assists
            explosiveness = min(1.2, 1.0 + (player.goals_scored * 0.03))

            captain_raw = breakdown.final_score * home_bonus * fdr_factor * explosiveness
            captain_index = round(min(99.0, max(10.0, captain_raw)), 1)

            scored_candidates.append({
                "player": player,
                "captain_index": captain_index,
                "breakdown": breakdown,
                "opponent": opponent_name or "TBD",
                "is_home": is_home,
                "fdr": fdr,
            })

        # Sort descending by captain index
        scored_candidates.sort(key=lambda x: x["captain_index"], reverse=True)
        top_picks = scored_candidates[:top_n]

        results = []
        for rank, item in enumerate(top_picks, start=1):
            p = item["player"]
            c_idx = item["captain_index"]
            loc = "Home" if item["is_home"] else "Away"
            opp = item["opponent"]
            fdr = item["fdr"]

            if rank == 1:
                tier_label = "Primary Captain Choice"
                color = "#00FF87"
                rationale = f"Leading candidate with peak ceiling. Plays {loc} vs {opp} (FDR {fdr}) with form {p.form}."
            elif rank == 2:
                tier_label = "Safe Vice-Captain / Co-Captain"
                color = "#01FC7A"
                rationale = f"High-floor alternative with dependable minutes. Faces {opp} ({loc})."
            elif rank <= 3:
                tier_label = "Strong Form Option"
                color = "#00BFFF"
                rationale = f"Excellent underlying threat (xG: {p.expected_goals:.1f}). Strong contender for armband."
            else:
                tier_label = "Differential Armband Pick"
                color = "#FFD700"
                rationale = f"Low ownership ceiling play. Massive upside if template captains fail."

            results.append(CaptainSuggestion(
                rank=rank,
                player=p,
                captain_index=c_idx,
                tier_label=tier_label,
                badge_color=color,
                next_opponent=opp,
                is_home=item["is_home"],
                fdr=fdr,
                score_breakdown=item["breakdown"],
                rationale=rationale,
            ))

        return results
