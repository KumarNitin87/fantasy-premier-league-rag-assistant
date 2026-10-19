"""
Advanced FPL performance and expected statistics calculations.
"""

from typing import List
from src.database.models import Player, Fixture


def calculate_xgi90(player: Player) -> float:
    """Calculate expected goal involvements per 90 minutes."""
    if player.minutes < 90:
        return 0.0
    return round((player.expected_goal_involvements / player.minutes) * 90.0, 2)


def calculate_xg90(player: Player) -> float:
    """Calculate expected goals per 90 minutes."""
    if player.minutes < 90:
        return 0.0
    return round((player.expected_goals / player.minutes) * 90.0, 2)


def calculate_xa90(player: Player) -> float:
    """Calculate expected assists per 90 minutes."""
    if player.minutes < 90:
        return 0.0
    return round((player.expected_assists / player.minutes) * 90.0, 2)


def calculate_ppm(player: Player) -> float:
    """Calculate Points Per Million cost."""
    cost_millions = max(player.now_cost / 10.0, 4.0)
    return round(player.total_points / cost_millions, 2)


def calculate_value_form(player: Player) -> float:
    """Calculate Form value per million cost."""
    cost_millions = max(player.now_cost / 10.0, 4.0)
    return round(player.form / cost_millions, 2)


def calculate_avg_fdr(fixtures: List[Fixture], team_id: int, num_matches: int = 3) -> float:
    """
    Calculate average Fixture Difficulty Rating for the upcoming N matches.
    Scale: 1.0 (easiest) to 5.0 (hardest). Defaults to 3.0 (neutral).
    """
    if not fixtures:
        return 3.0

    selected = fixtures[:num_matches]
    ratings = []
    for fx in selected:
        if fx.team_h == team_id:
            ratings.append(fx.team_h_difficulty)
        else:
            ratings.append(fx.team_a_difficulty)

    return round(sum(ratings) / len(ratings), 2) if ratings else 3.0
