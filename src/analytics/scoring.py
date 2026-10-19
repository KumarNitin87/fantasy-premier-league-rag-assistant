"""
Decision Confidence Scoring Engine: Deterministic, interpretable 0-100% confidence rubric.
"""

from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Dict, Any, Optional
import math
import logging
import time

from src.database.models import Player, Fixture, Team
from src.analytics.metrics import calculate_xgi90, calculate_ppm, calculate_avg_fdr
from config.constants import CONFIDENCE_WEIGHTS, AVAILABILITY_PENALTIES, ROTATION_PENALTY, NEWS_SENTIMENT_PENALTY

logger = logging.getLogger(__name__)

# Older ingestion/model defaults wrote 1000 when the source omitted a rating.
# Existing rows have no provenance, so this exact legacy sentinel is unsafe to
# treat as measured team strength.
LEGACY_TEAM_STRENGTH_FALLBACK = 1000.0


@dataclass
class ScoreBreakdown:
    """Interpretable breakdown of confidence sub-factors and applied penalties."""
    final_score: float
    tier: str  # "Elite Pick", "Strong Recommendation", "Moderate", "Punt / Risky", "Avoid"
    tier_color: str
    base_score: float
    subscores: Dict[str, float] = field(default_factory=dict)
    penalties_applied: List[Dict[str, Any]] = field(default_factory=list)
    positive_reasons: List[str] = field(default_factory=list)
    negative_reasons: List[str] = field(default_factory=list)
    summary_verdict: str = ""


class DecisionScoringEngine:
    """Computes deterministic, multi-factor confidence scores for FPL decisions."""

    TEAM_CONTEXT_CACHE_SECONDS = 300

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        team_provider: Optional[Callable[[], Iterable[Team]]] = None,
    ):
        self.weights = weights or CONFIDENCE_WEIGHTS
        self.team_provider = team_provider
        self._team_lookup: Optional[Dict[int, Team]] = None
        self._team_lookup_loaded_at = 0.0

    def refresh_team_context(self) -> None:
        """Invalidate cached team ratings after the FPL team data is synchronized."""
        self._team_lookup = None
        self._team_lookup_loaded_at = 0.0

    def _get_team_lookup(self) -> Dict[int, Team]:
        if self.team_provider is None:
            return {}

        now = time.monotonic()
        if (
            self._team_lookup is not None
            and now - self._team_lookup_loaded_at < self.TEAM_CONTEXT_CACHE_SECONDS
        ):
            return self._team_lookup

        try:
            self._team_lookup = {team.id: team for team in self.team_provider()}
        except Exception:
            # Missing team context must reduce available scoring factors, not
            # break a page or silently synthesize a neutral 50% component.
            logger.warning("Team-strength context unavailable; Team Delta omitted.")
            self._team_lookup = {}
        self._team_lookup_loaded_at = now
        return self._team_lookup

    def evaluate_player(
        self,
        player: Player,
        upcoming_fixtures: List[Fixture],
        opponent_team: Optional[Team] = None,
        has_negative_news: bool = False,
    ) -> ScoreBreakdown:
        """
        Evaluate a single player and calculate Decision Confidence Score (0-100).
        """
        positive_reasons: List[str] = []
        negative_reasons: List[str] = []

        # 1. Form Factor (0 - 100)
        # Form scale: 0 to 10+
        form_norm = min(max(player.form / 10.0, 0.0), 1.0) * 100.0
        if player.form >= 6.0:
            positive_reasons.append(f"Excellent recent form ({player.form} pts/match)")
        elif player.form < 3.0:
            negative_reasons.append(f"Sluggish recent form ({player.form} pts/match)")

        # 2. Fixture Factor (0 - 100)
        # FDR 1 = 100%, FDR 2 = 75%, FDR 3 = 50%, FDR 4 = 25%, FDR 5 = 0%
        avg_fdr = calculate_avg_fdr(upcoming_fixtures, player.team_id, num_matches=3)
        fixture_norm = max(0.0, min(100.0, ((5.0 - avg_fdr) / 4.0) * 100.0))
        if avg_fdr <= 2.5:
            positive_reasons.append(f"Favorable upcoming fixtures (Avg FDR: {avg_fdr:.1f})")
        elif avg_fdr >= 3.8:
            negative_reasons.append(f"Tough upcoming schedule (Avg FDR: {avg_fdr:.1f})")

        # 3. Underlying Stats Factor (0 - 100)
        # xGI per 90: benchmark 0.70+ for elite attack
        xgi90 = calculate_xgi90(player)
        underlying_norm = min(max(xgi90 / 0.70, 0.0), 1.0) * 100.0
        if xgi90 >= 0.50:
            positive_reasons.append(f"Strong underlying threat (xGI/90: {xgi90:.2f})")
        elif xgi90 < 0.20 and player.position in {"MID", "FWD"}:
            negative_reasons.append(f"Low underlying involvement (xGI/90: {xgi90:.2f})")

        # 4. Value / Historic ROI Factor (0 - 100)
        # Points per million (PPM) benchmark: 10.0+
        ppm = calculate_ppm(player)
        value_norm = min(max(ppm / 10.0, 0.0), 1.0) * 100.0
        if ppm >= 8.0:
            positive_reasons.append(f"High points per million efficiency ({ppm:.1f} PPM)")
        elif ppm < 4.0 and player.minutes > 270:
            negative_reasons.append(f"Low return on investment ({ppm:.1f} PPM)")

        # 5. Team Strength Delta (0 - 100)
        # Compare the player's club attack with the opposing club defence for
        # each of the next three fixtures, respecting home/away venue.
        team_lookup = self._get_team_lookup()
        if opponent_team:
            team_lookup = {**team_lookup, opponent_team.id: opponent_team}
        team_delta_norm = calculate_team_delta(player.team_id, upcoming_fixtures, team_lookup)

        # Only include a team-context factor when both clubs have usable source
        # ratings. Renormalize the available deterministic factors to sum to 1.
        raw_subscores = {
            "form": round(form_norm, 1),
            "fixture": round(fixture_norm, 1),
            "underlying": round(underlying_norm, 1),
            "value": round(value_norm, 1),
        }
        if team_delta_norm is not None:
            raw_subscores["team_delta"] = round(team_delta_norm, 1)

        subscores = raw_subscores
        active_weight_total = sum(
            self.weights.get(name, 0.0) for name in raw_subscores
        )
        base_score = (
            sum(raw_subscores[name] * self.weights.get(name, 0.0) for name in raw_subscores)
            / active_weight_total
            if active_weight_total > 0
            else 0.0
        )

        # 6. Apply Penalties
        penalties_applied: List[Dict[str, Any]] = []
        total_multiplier = 1.0

        # Availability Penalty
        chance = player.chance_of_playing_next_round
        if chance is None:
            chance = 100 if player.status == "a" else 0

        avail_mult = AVAILABILITY_PENALTIES.get(chance, 0.50 if chance < 75 else 1.0)
        if player.status in {"i", "s", "u"} or chance == 0:
            avail_mult = 0.0

        if avail_mult < 1.0:
            penalties_applied.append({
                "factor": "Injury / Availability Risk",
                "multiplier": avail_mult,
                "reason": f"Status: '{player.status.upper()}' with {chance}% chance of playing",
            })
            negative_reasons.append(f"Availability concern: {chance}% playing chance ({player.news or 'Unavailable'})")
            total_multiplier *= avail_mult

        # Rotation Risk Penalty (if played less than 50 mins/match on average)
        if player.minutes > 0 and (player.minutes / max(player.total_points, 1)) < 30.0 and player.minutes < 180:
            penalties_applied.append({
                "factor": "Minutes / Rotation Risk",
                "multiplier": ROTATION_PENALTY,
                "reason": "Limited starts or inconsistent minutes profile",
            })
            negative_reasons.append("Minutes risk: Not an undisputed 90-minute starter")
            total_multiplier *= ROTATION_PENALTY

        # News Adverse Sentiment Penalty
        if has_negative_news and avail_mult > 0:
            penalties_applied.append({
                "factor": "Adverse Press / News Sentiment",
                "multiplier": NEWS_SENTIMENT_PENALTY,
                "reason": "Recent press conference or medical bulletin flagged concern",
            })
            negative_reasons.append("Press conference flagged minor concern or knock")
            total_multiplier *= NEWS_SENTIMENT_PENALTY

        # Final Score
        final_score = round(max(0.0, min(100.0, base_score * total_multiplier)), 1)

        # Determine Tier & Color
        if final_score >= 80.0:
            tier = "Elite Pick"
            tier_color = "#00FF87"  # Green
            summary = f"Exceptional target with peak form and favorable fixture run."
        elif final_score >= 65.0:
            tier = "Strong Recommendation"
            tier_color = "#01FC7A"  # Mint
            summary = f"Solid option offering reliable expected point returns."
        elif final_score >= 45.0:
            tier = "Moderate / Template"
            tier_color = "#FFD700"  # Yellow
            summary = f"Viable pick but tempered by fixture difficulty or underlying variance."
        elif final_score >= 25.0:
            tier = "Punt / High Risk"
            tier_color = "#FF8C00"  # Orange
            summary = f"Speculative differential with substantial rotation or output risk."
        else:
            tier = "Avoid / High Hazard"
            tier_color = "#DC3545"  # Red
            summary = f"Severely compromised by injury, suspension, or poor underlying numbers."

        return ScoreBreakdown(
            final_score=final_score,
            tier=tier,
            tier_color=tier_color,
            base_score=round(base_score, 1),
            subscores=subscores,
            penalties_applied=penalties_applied,
            positive_reasons=positive_reasons,
            negative_reasons=negative_reasons,
            summary_verdict=summary,
        )


def calculate_team_delta(
    player_team_id: int,
    upcoming_fixtures: Iterable[Fixture],
    teams_by_id: Dict[int, Team],
    fixture_limit: int = 3,
) -> Optional[float]:
    """Return a normalized attack-vs-opponent-defence rating, or None.

    For each of the next ``fixture_limit`` club fixtures, compare the player's
    team's venue-specific attack rating with the opponent's defence rating at
    its corresponding venue. The mean rating difference maps linearly around
    neutral 50 at 20 rating
    points per score point, clamped to [0, 100]. Zero/missing/non-numeric
    ratings are treated as unavailable rather than as neutral values.
    """
    player_team = teams_by_id.get(player_team_id)
    if player_team is None or fixture_limit <= 0:
        return None

    deltas: List[float] = []
    club_fixtures = 0
    for fixture in upcoming_fixtures:
        if getattr(fixture, "finished", False):
            continue
        if fixture.team_h == player_team_id:
            opponent_id = fixture.team_a
            attack_rating = player_team.strength_attack_home
            opponent = teams_by_id.get(opponent_id)
            defence_rating = opponent.strength_defence_away if opponent else None
        elif fixture.team_a == player_team_id:
            opponent_id = fixture.team_h
            attack_rating = player_team.strength_attack_away
            opponent = teams_by_id.get(opponent_id)
            defence_rating = opponent.strength_defence_home if opponent else None
        else:
            continue

        if club_fixtures >= fixture_limit:
            break
        club_fixtures += 1

        try:
            attack_value = float(attack_rating)
            defence_value = float(defence_rating)
        except (TypeError, ValueError):
            continue
        if (
            not math.isfinite(attack_value)
            or not math.isfinite(defence_value)
            or attack_value <= 0
            or defence_value <= 0
            or attack_value == LEGACY_TEAM_STRENGTH_FALLBACK
            or defence_value == LEGACY_TEAM_STRENGTH_FALLBACK
        ):
            continue
        deltas.append(attack_value - defence_value)

    if not deltas:
        return None

    mean_delta = sum(deltas) / len(deltas)
    return round(max(0.0, min(100.0, 50.0 + mean_delta / 20.0)), 1)
