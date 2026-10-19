"""
Unit tests for the Decision Confidence Scoring Engine.
"""

import pytest
from dataclasses import replace
from src.database.models import Player, Fixture, Team
from src.analytics.scoring import DecisionScoringEngine, calculate_team_delta
from src.analytics.metrics import calculate_avg_fdr, calculate_xgi90, calculate_ppm


@pytest.fixture
def scoring_engine():
    return DecisionScoringEngine()


@pytest.fixture
def mock_player():
    return Player(
        id=1,
        team_id=1,
        web_name="Saka",
        first_name="Bukayo",
        second_name="Saka",
        position="MID",
        element_type=3,
        now_cost=100,  # £10.0m
        form=8.0,
        total_points=50,
        minutes=360,
        goals_scored=4,
        assists=3,
        clean_sheets=2,
        expected_goals=3.2,
        expected_assists=2.5,
        expected_goal_involvements=5.7,
        status="a",
        chance_of_playing_next_round=100,
    )


@pytest.fixture
def mock_fixtures():
    return [
        Fixture(id=1, event=6, team_h=1, team_a=2, team_h_difficulty=2, team_a_difficulty=4),
        Fixture(id=2, event=7, team_h=3, team_a=1, team_h_difficulty=5, team_a_difficulty=2),
        Fixture(id=3, event=8, team_h=1, team_a=4, team_h_difficulty=3, team_a_difficulty=3),
    ]


def test_decision_scoring_healthy_elite(scoring_engine, mock_player, mock_fixtures):
    """A healthy, in-form player with easy fixtures should achieve a High/Elite score."""
    breakdown = scoring_engine.evaluate_player(mock_player, mock_fixtures)
    assert breakdown.final_score > 60.0
    assert breakdown.tier in {"Elite Pick", "Strong Recommendation"}
    assert len(breakdown.positive_reasons) > 0
    assert len(breakdown.penalties_applied) == 0


def test_decision_scoring_injured_veto(scoring_engine, mock_player, mock_fixtures):
    """An injured player with 0% chance of playing must receive a 0.0 confidence score."""
    mock_player.status = "i"
    mock_player.chance_of_playing_next_round = 0

    breakdown = scoring_engine.evaluate_player(mock_player, mock_fixtures)
    assert breakdown.final_score == 0.0
    assert breakdown.tier == "Avoid / High Hazard"
    assert any(p["factor"] == "Injury / Availability Risk" for p in breakdown.penalties_applied)


def test_decision_scoring_doubtful_penalty(scoring_engine, mock_player, mock_fixtures):
    """A doubtful player (75% chance) should receive a 0.75 penalty multiplier."""
    mock_player.status = "d"
    mock_player.chance_of_playing_next_round = 75

    breakdown = scoring_engine.evaluate_player(mock_player, mock_fixtures)
    assert breakdown.final_score < breakdown.base_score
    assert any(p["multiplier"] == 0.75 for p in breakdown.penalties_applied)


def test_metrics_calculations(mock_player, mock_fixtures):
    """Verify xGI/90, PPM, and FDR metric accuracy."""
    xgi90 = calculate_xgi90(mock_player)
    assert xgi90 > 0.5

    ppm = calculate_ppm(mock_player)
    assert ppm == 5.0  # 50 points / 10.0m

    avg_fdr = calculate_avg_fdr(mock_fixtures, team_id=1, num_matches=3)
    # Fixture 1: team_h=1 -> difficulty=2
    # Fixture 2: team_a=1 -> difficulty=2
    # Fixture 3: team_h=1 -> difficulty=3
    # Avg: (2 + 2 + 3) / 3 = 2.33
    assert avg_fdr == pytest.approx(2.33, 0.05)


def test_team_delta_varies_with_club_and_opponent_strength(mock_player):
    """Venue-specific team context changes the component across clubs."""
    teams = [
        Team(1, "Attackers", "ATT", strength_attack_home=1200),
        Team(2, "Average Defence", "AVG", strength_defence_away=1100),
        Team(3, "Weaker Attack", "WEK", strength_attack_home=900),
        Team(4, "Average Defence 2", "AVG2", strength_defence_away=1100),
    ]
    engine = DecisionScoringEngine(team_provider=lambda: teams)
    fixture_a = [Fixture(id=1, event=1, team_h=1, team_a=2)]
    fixture_b = [Fixture(id=2, event=1, team_h=3, team_a=4)]

    strong_context = engine.evaluate_player(mock_player, fixture_a)
    weaker_player = replace(mock_player, id=2, team_id=3)
    weak_context = engine.evaluate_player(weaker_player, fixture_b)

    assert strong_context.subscores["team_delta"] == 55.0
    assert weak_context.subscores["team_delta"] == 40.0
    assert strong_context.subscores["team_delta"] != weak_context.subscores["team_delta"]


def test_players_from_same_team_share_team_context_component(mock_player):
    """Player-level stats do not alter a club-fixture context component."""
    teams = [
        Team(1, "Club One", "ONE", strength_attack_home=1200),
        Team(2, "Club Two", "TWO", strength_defence_away=1100),
    ]
    engine = DecisionScoringEngine(team_provider=lambda: teams)
    fixtures = [Fixture(id=1, event=1, team_h=1, team_a=2)]
    other_player = replace(mock_player, id=2, web_name="Teammate", form=2.0)

    first = engine.evaluate_player(mock_player, fixtures)
    second = engine.evaluate_player(other_player, fixtures)

    assert first.subscores["team_delta"] == second.subscores["team_delta"] == 55.0


def test_missing_team_ratings_omit_team_delta_safely(mock_player):
    """Unavailable ratings do not become a fabricated neutral score."""
    teams = [Team(1, "Club One", "ONE"), Team(2, "Club Two", "TWO")]
    fixture = Fixture(id=1, event=1, team_h=1, team_a=2)
    engine = DecisionScoringEngine(team_provider=lambda: teams)

    result = engine.evaluate_player(mock_player, [fixture])

    assert "team_delta" not in result.subscores
    assert 0.0 <= result.final_score <= 100.0
    assert calculate_team_delta(1, [fixture], {}) is None


def test_legacy_neutral_rating_fallback_is_not_scored(mock_player):
    """Old rows with synthetic 1000 ratings do not display a false 50%."""
    teams = [
        Team(1, "Club One", "ONE", strength_attack_home=1000),
        Team(2, "Club Two", "TWO", strength_defence_away=1000),
    ]
    fixture = Fixture(id=1, event=1, team_h=1, team_a=2)
    result = DecisionScoringEngine(team_provider=lambda: teams).evaluate_player(
        mock_player, [fixture]
    )

    assert "team_delta" not in result.subscores


def test_neutral_team_delta_is_allowed_when_measured_ratings_match():
    """50 is valid when both sourced ratings genuinely match."""
    teams = [
        Team(1, "Club One", "ONE", strength_attack_home=1100),
        Team(2, "Club Two", "TWO", strength_defence_away=1100),
    ]
    fixture = Fixture(id=1, event=1, team_h=1, team_a=2)

    assert calculate_team_delta(1, [fixture], {team.id: team for team in teams}) == 50.0


def test_team_delta_uses_away_attack_and_home_opponent_defence():
    """Away fixtures use the away attack/home defence fields, not home fields."""
    teams = [
        Team(
            1, "Club One", "ONE",
            strength_attack_home=1500,
            strength_attack_away=1200,
        ),
        Team(
            2, "Club Two", "TWO",
            strength_defence_home=1100,
            strength_defence_away=900,
        ),
    ]
    away_fixture = Fixture(id=1, event=1, team_h=2, team_a=1)

    result = calculate_team_delta(
        1, [away_fixture], {team.id: team for team in teams}
    )

    assert result == 55.0


def test_fdr_remains_separate_from_team_delta(mock_player):
    """Changing FDR changes Fixture Ease, not the strength-based Team Delta."""
    teams = [
        Team(1, "Club One", "ONE", strength_attack_home=1200),
        Team(2, "Club Two", "TWO", strength_defence_away=1100),
    ]
    engine = DecisionScoringEngine(team_provider=lambda: teams)
    easy_fixture = Fixture(
        id=1, event=1, team_h=1, team_a=2,
        team_h_difficulty=1, team_a_difficulty=5,
    )
    hard_fixture = Fixture(
        id=2, event=1, team_h=1, team_a=2,
        team_h_difficulty=5, team_a_difficulty=1,
    )

    easy = engine.evaluate_player(mock_player, [easy_fixture])
    hard = engine.evaluate_player(mock_player, [hard_fixture])

    assert easy.subscores["team_delta"] == hard.subscores["team_delta"] == 55.0
    assert easy.subscores["fixture"] != hard.subscores["fixture"]


def test_team_delta_weight_and_available_factor_renormalization(mock_player, mock_fixtures):
    """The 15% weight is retained when present and excluded when unavailable."""
    teams_with_ratings = [
        Team(1, "Club One", "ONE", strength_attack_home=1200),
        Team(2, "Club Two", "TWO", strength_defence_away=1100),
    ]
    available_engine = DecisionScoringEngine(team_provider=lambda: teams_with_ratings)
    available = available_engine.evaluate_player(mock_player, mock_fixtures)
    assert available_engine.weights["team_delta"] == pytest.approx(0.15)
    available_names = tuple(available.subscores)
    expected_available = sum(
        available.subscores[name] * available_engine.weights[name]
        for name in available_names
    ) / sum(available_engine.weights[name] for name in available_names)
    assert available.base_score == pytest.approx(expected_available, abs=0.05)

    missing_engine = DecisionScoringEngine(
        team_provider=lambda: [Team(1, "Club One", "ONE"), Team(2, "Club Two", "TWO")]
    )
    missing = missing_engine.evaluate_player(mock_player, mock_fixtures)
    assert "team_delta" not in missing.subscores
    missing_names = tuple(missing.subscores)
    expected_missing = sum(
        missing.subscores[name] * missing_engine.weights[name]
        for name in missing_names
    ) / sum(missing_engine.weights[name] for name in missing_names)
    assert sum(missing_engine.weights[name] for name in missing_names) == pytest.approx(0.85)
    assert missing.base_score == pytest.approx(expected_missing, abs=0.05)


def test_team_delta_is_not_a_universal_neutral_default(mock_player, mock_fixtures):
    """Without sourced team context the factor is unavailable, not 50%."""
    result = DecisionScoringEngine().evaluate_player(mock_player, mock_fixtures)

    assert "team_delta" not in result.subscores
