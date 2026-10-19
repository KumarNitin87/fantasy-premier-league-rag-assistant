"""
Transfer Decision Evaluator: Evaluates Player OUT vs Player IN moves.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from src.database.models import Player, Fixture
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine, ScoreBreakdown
from src.analytics.metrics import calculate_xgi90, calculate_avg_fdr


@dataclass
class TransferEvaluation:
    """Detailed evaluation result for a transfer move."""
    player_out: Player
    player_in: Player
    cost_diff: float  # in millions (+ means requires extra money, - means saves money)
    is_affordable: bool
    score_out: ScoreBreakdown
    score_in: ScoreBreakdown
    confidence_delta: float
    verdict: str
    verdict_badge_color: str
    rationale: List[str]


class TransferAnalyzer:
    """Analyzes and rates proposed player transfers."""

    def __init__(self, scoring_engine: Optional[DecisionScoringEngine] = None, repository: Optional[FPLRepository] = None):
        self.repo = repository or FPLRepository()
        self.scoring = scoring_engine or DecisionScoringEngine(
            team_provider=self.repo.get_all_teams
        )

    def evaluate(
        self,
        player_out: Player,
        player_in: Player,
        bank_balance: float = 0.0,
        has_negative_news_out: bool = False,
        has_negative_news_in: bool = False,
    ) -> TransferEvaluation:
        """Evaluate transferring player_out to player_in."""
        cost_diff = round(player_in.cost_float - player_out.cost_float, 1)
        is_affordable = cost_diff <= bank_balance

        # Get fixtures for both
        fx_out = self.repo.get_upcoming_fixtures(team_id=player_out.team_id, limit=3)
        fx_in = self.repo.get_upcoming_fixtures(team_id=player_in.team_id, limit=3)

        score_out = self.scoring.evaluate_player(player_out, fx_out, has_negative_news=has_negative_news_out)
        score_in = self.scoring.evaluate_player(player_in, fx_in, has_negative_news=has_negative_news_in)

        confidence_delta = round(score_in.final_score - score_out.final_score, 1)
        rationale: List[str] = []

        # Rationale builder
        if not is_affordable:
            rationale.append(f"Requires £{cost_diff:.1f}m but you only have £{bank_balance:.1f}m in the bank.")

        if score_in.final_score > score_out.final_score:
            rationale.append(f"Confidence score gain: +{confidence_delta:.1f} pts ({score_in.tier} vs {score_out.tier}).")
        else:
            rationale.append(f"Confidence score deficit: {confidence_delta:.1f} pts ({score_in.tier} vs {score_out.tier}).")

        fdr_in = calculate_avg_fdr(fx_in, player_in.team_id)
        fdr_out = calculate_avg_fdr(fx_out, player_out.team_id)
        if fdr_in < fdr_out:
            rationale.append(f"Fixture improvement: {player_in.web_name} has easier fixtures (FDR {fdr_in:.1f} vs {fdr_out:.1f}).")
        elif fdr_in > fdr_out:
            rationale.append(f"Fixture downgrade: {player_in.web_name} faces a tougher run (FDR {fdr_in:.1f} vs {fdr_out:.1f}).")

        xgi_in = calculate_xgi90(player_in)
        xgi_out = calculate_xgi90(player_out)
        if xgi_in > xgi_out:
            rationale.append(f"Underlying boost: Higher xGI/90 ({xgi_in:.2f} vs {xgi_out:.2f}).")

        # Determine Verdict
        if not is_affordable:
            verdict = "Transfer Unaffordable"
            color = "#DC3545"
        elif confidence_delta >= 20.0:
            verdict = "Strong Upgrade (Highly Recommended)"
            color = "#00FF87"
        elif confidence_delta >= 8.0:
            verdict = "Favorable Move"
            color = "#01FC7A"
        elif confidence_delta >= -5.0:
            verdict = "Sideways Move / Neutral"
            color = "#FFD700"
        else:
            verdict = "Downgrade (Not Recommended)"
            color = "#DC3545"

        return TransferEvaluation(
            player_out=player_out,
            player_in=player_in,
            cost_diff=cost_diff,
            is_affordable=is_affordable,
            score_out=score_out,
            score_in=score_in,
            confidence_delta=confidence_delta,
            verdict=verdict,
            verdict_badge_color=color,
            rationale=rationale,
        )
