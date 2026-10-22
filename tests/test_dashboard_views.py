"""Focused interaction-data regression tests for dashboard views."""

import pandas as pd

from src.database.models import Fixture, Player
from ui.views.comparison_view import _comparison_dataframe
from ui.views.fixtures_view import _fixture_score, _fixture_status
from ui.views.stats_explorer import _filter_player_results


def _player(player_id, *, team_id=1, position="MID", cost=8.0, status="a"):
    return Player(
        id=player_id,
        team_id=team_id,
        web_name=f"Player {player_id}",
        first_name="Test",
        second_name=f"Player {player_id}",
        position=position,
        element_type={"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
        now_cost=int(cost * 10),
        status=status,
    )


def test_player_stats_filters_search_results_by_position_team_price_and_availability():
    players = [
        _player(1, team_id=1, position="MID", cost=8.0, status="a"),
        _player(2, team_id=1, position="MID", cost=9.0, status="d"),
        _player(3, team_id=2, position="MID", cost=7.0, status="a"),
        _player(4, team_id=1, position="DEF", cost=7.0, status="a"),
    ]

    assert [p.id for p in _filter_player_results(
        players, position="MID", team_id=1, max_cost=8.5, availability="Available (Fit)"
    )] == [1]
    assert [p.id for p in _filter_player_results(
        players, position="MID", team_id=1, max_cost=10, availability="Injured / Doubtful"
    )] == [2]
    assert _filter_player_results(players, team_id=99) == []


def test_fixtures_view_formats_empty_scores_and_live_finished_states():
    fixture = Fixture(
        id=1, event=4, team_h=1, team_a=2, team_h_difficulty=3, team_a_difficulty=4,
        started=True, finished=False,
    )
    assert _fixture_score(fixture) == "vs"
    assert _fixture_status(fixture) == "Live"

    fixture.team_h_score = 2
    fixture.team_a_score = 0
    fixture.finished = True
    assert _fixture_score(fixture) == "2 - 0"
    assert _fixture_status(fixture) == "Final"

    fixture.finished_provisional = True
    assert _fixture_status(fixture) == "Finished · FPL points provisional"


def test_comparison_view_serializes_missing_mixed_metrics_for_streamlit():
    comparison = _comparison_dataframe([
        {"Metric": "Form", "A": 5.2, "B": None},
        {"Metric": "Status", "A": "a", "B": 75},
    ])

    assert isinstance(comparison, pd.DataFrame)
    assert comparison.loc[0, "A"] == "5.2"
    assert comparison.loc[0, "B"] == ""
    assert comparison.loc[1, "B"] == "75.0"
