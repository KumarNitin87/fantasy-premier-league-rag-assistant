"""Player profile data tests using an isolated temporary SQLite database."""

from pathlib import Path

import pytest

from src.database.connection import init_db
from src.database.models import Fixture, Player, PlayerGameweekHistory, Team
from src.database.repository import FPLRepository
from ui.components.player_profile import (
    availability_label,
    format_profile_value,
    latest_gameweek_rows,
    per90_profile_metrics,
    player_metric_cards,
    profile_data,
    profile_header_values,
)


@pytest.fixture
def profile_repo(tmp_path: Path):
    database = tmp_path / "player-profile-test.sqlite"
    init_db(database)
    repo = FPLRepository(db_path=database)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Manchester City", short_name="MCI"),
        Team(id=3, name="Liverpool", short_name="LIV"),
        Team(id=4, name="Fulham", short_name="FUL"),
        Team(id=5, name="Everton", short_name="EVE"),
        Team(id=6, name="Brighton", short_name="BHA"),
        Team(id=7, name="Brentford", short_name="BRE"),
    ])
    repo.upsert_players([
        Player(
            id=101, team_id=1, web_name="Saka", first_name="Bukayo", second_name="Saka",
            position="MID", element_type=3, now_cost=100, code=243298,
            selected_by_percent=35.2, form=7.4, total_points=88, minutes=900,
            goals_scored=6, assists=5, clean_sheets=4, expected_goals=4.2,
            expected_assists=3.1, expected_goal_involvements=7.3, ict_index=92.5,
        ),
        Player(
            id=102, team_id=1, web_name="Raya", first_name="David", second_name="Raya",
            position="GKP", element_type=1, now_cost=55, code=10002,
            minutes=900, saves=31, clean_sheets=6, goals_conceded=8,
            expected_goals_conceded=9.1,
        ),
        Player(
            id=103, team_id=2, web_name="Stones", first_name="John", second_name="Stones",
            position="DEF", element_type=2, now_cost=52, code=10003,
            minutes=900, goals_scored=1, assists=1, clean_sheets=5,
            expected_goals_conceded=8.7, bps=190,
        ),
        Player(
            id=104, team_id=3, web_name="Forward", first_name="Sample", second_name="Forward",
            position="FWD", element_type=4, now_cost=75, code=10004,
            minutes=900, goals_scored=4, assists=2, expected_goals=3.8,
            expected_assists=1.4, expected_goal_involvements=5.2, ict_index=62.0,
        ),
    ])
    repo.upsert_gameweek_history([
        PlayerGameweekHistory(
            player_id=101, round=round_number, total_points=round_number * 2,
            minutes=90,
        )
        for round_number in range(1, 8)
    ])
    return repo


def test_exact_player_search_returns_expected_record(profile_repo):
    matches = profile_repo.search_players("Saka")
    assert [player.id for player in matches] == [101]
    assert matches[0].web_name == "Saka"


def test_profile_header_and_metrics_use_player_record(profile_repo):
    player = profile_repo.get_player_by_id(101)
    header = profile_header_values(player)
    metrics = dict(player_metric_cards(player))

    assert header["name"] == "Bukayo Saka"
    assert header["team"] == "Arsenal"
    assert header["position_code"] == "MID"
    assert header["player_id"] == 101
    assert header["price"] == 10.0
    assert header["form"] == 7.4
    assert header["total_points"] == 88
    assert header["selected_by_percent"] == 35.2
    assert header["availability"] == "Available"
    assert metrics["Total Points"] == 88
    assert metrics["Goals"] == 6
    assert metrics["xG"] == 4.2
    assert metrics["xA"] == 3.1


def test_profile_photo_uses_the_existing_central_resolver(profile_repo, monkeypatch):
    from ui.components import player_photo

    player = profile_repo.get_player_by_id(101)
    monkeypatch.setattr(player_photo, "_official_photo_is_available", lambda url: True)

    photo_url = player_photo.resolve_player_photo_url(player, profile_repo)

    assert photo_url.endswith(f"/p{player.code}.png")


def test_latest_five_gameweeks_are_selected_and_chronological():
    history = [
        PlayerGameweekHistory(player_id=101, round=round_number, total_points=round_number * 2)
        for round_number in range(1, 8)
    ]
    assert latest_gameweek_rows(history, limit=5) == [
        {"Gameweek": "GW3", "Points": 6},
        {"Gameweek": "GW4", "Points": 8},
        {"Gameweek": "GW5", "Points": 10},
        {"Gameweek": "GW6", "Points": 12},
        {"Gameweek": "GW7", "Points": 14},
    ]
    assert latest_gameweek_rows(history[:2], limit=5) == [
        {"Gameweek": "GW1", "Points": 2},
        {"Gameweek": "GW2", "Points": 4},
    ]


def test_recent_form_marks_provisional_values_without_losing_zero_point_rounds():
    history = [
        PlayerGameweekHistory(player_id=101, round=6, total_points=0, is_provisional=True),
        PlayerGameweekHistory(player_id=101, round=7, total_points=4),
    ]

    assert latest_gameweek_rows(history) == [
        {"Gameweek": "GW6 *", "Points": 0},
        {"Gameweek": "GW7", "Points": 4},
    ]


def test_profile_uses_five_upcoming_database_fixtures_and_fdr(profile_repo):
    fixture_data = [
        Fixture(
            id=fixture_id, event=fixture_id, team_h=1, team_a=opponent,
            team_h_difficulty=home_fdr, team_a_difficulty=away_fdr,
            finished=False, kickoff_time=f"2026-10-{fixture_id + 10:02d}T15:00:00Z",
        )
        for fixture_id, opponent, home_fdr, away_fdr in [
            (1, 2, 2, 4), (2, 3, 3, 3), (3, 4, 1, 5), (4, 5, 4, 2), (5, 6, 2, 4),
        ]
    ]
    # A sixth row confirms the repository limit is honored.
    fixture_data.append(Fixture(
        id=6, event=6, team_h=1, team_a=7, team_h_difficulty=5,
        team_a_difficulty=1, finished=False, kickoff_time="2026-11-01T15:00:00Z",
    ))
    profile_repo.upsert_fixtures(fixture_data)

    result = profile_data(profile_repo, profile_repo.get_player_by_id(101))
    assert result["history"] == [
        {"Gameweek": f"GW{week}", "Points": week * 2}
        for week in range(3, 8)
    ]
    assert len(result["fixtures"]) == 5
    assert result["fixtures"][0] == {
        "Gameweek": "GW1", "Opponent": "MCI", "Venue": "Home (H)",
        "FDR": 2, "Date": "Sun, 11 Oct 2026",
    }
    assert [row["FDR"] for row in result["fixtures"]] == [2, 3, 1, 4, 2]


def test_missing_metric_is_preserved_as_unavailable_not_filled():
    player = Player(
        id=1, team_id=1, web_name="Example", first_name="Test", second_name="Player",
        position="MID", element_type=3, now_cost=60, expected_goals=None,
    )
    metrics = dict(player_metric_cards(player))
    assert metrics["xG"] is None
    assert format_profile_value(metrics["xG"]) == "N/A"


@pytest.mark.parametrize(
    ("position", "expected_metric"),
    [("GKP", "Saves / 90"), ("DEF", "Clean sheets / 90"), ("MID", "xG / 90"), ("FWD", "xG / 90")],
)
def test_per90_radar_metrics_adapt_to_player_position(profile_repo, position, expected_metric):
    player_id = {"GKP": 102, "DEF": 103, "MID": 101, "FWD": 104}[position]
    player = profile_repo.get_player_by_id(player_id)
    metrics = per90_profile_metrics(player)

    assert expected_metric in {label for label, _, _ in metrics}
    assert all(actual >= 0 and scale > 0 for _, actual, scale in metrics)


def test_per90_metrics_are_unavailable_without_played_minutes():
    player = Player(
        id=1, team_id=1, web_name="Example", first_name="Test", second_name="Player",
        position="FWD", element_type=4, now_cost=60, minutes=0,
    )
    assert per90_profile_metrics(player) == []


@pytest.mark.parametrize(
    ("status", "chance", "expected"),
    [("a", 100, "Available"), ("d", 75, "Doubtful (75% chance of playing)"),
     ("i", 0, "Injured"), ("s", 0, "Suspended"), ("u", 0, "Unavailable")],
)
def test_availability_label_uses_fpl_status(status, chance, expected):
    player = Player(
        id=1, team_id=1, web_name="Example", first_name="Test", second_name="Player",
        position="MID", element_type=3, now_cost=60, status=status,
        chance_of_playing_next_round=chance,
    )
    assert availability_label(player)[0] == expected
