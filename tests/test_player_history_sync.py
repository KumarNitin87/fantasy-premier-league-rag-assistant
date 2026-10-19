from pathlib import Path

import pytest

from src.database.connection import init_db
from src.database.models import Fixture, Player, PlayerGameweekHistory, Team
from src.database.repository import FPLRepository
from src.ingestion import sync_fpl as sync_fpl_module
from src.ingestion.sync_fpl import FPLSyncPipeline


class MappingHistoryClient:
    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    def get_element_summary(self, player_id, force_refresh=False):
        self.calls.append(player_id)
        response = self.histories[player_id]
        if isinstance(response, Exception):
            raise response
        return response


def create_repository(path: Path, player_count: int = 2) -> FPLRepository:
    init_db(path)
    repository = FPLRepository(db_path=path)
    repository.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    positions = ("GKP", "DEF", "MID", "FWD")
    repository.upsert_players([
        Player(
            id=player_id,
            team_id=1,
            web_name=f"Player{player_id}",
            first_name="Test",
            second_name=f"Player{player_id}",
            position=positions[(player_id - 1) % len(positions)],
            element_type=((player_id - 1) % len(positions)) + 1,
            now_cost=50,
            total_points=player_id,
        )
        for player_id in range(1, player_count + 1)
    ])
    repository.upsert_fixtures([
        Fixture(
            id=101, event=1, team_h=1, team_a=2,
            finished=True, started=True, kickoff_time="2026-08-01T12:00:00Z",
        ),
        Fixture(
            id=102, event=2, team_h=1, team_a=2,
            finished_provisional=True, started=True, kickoff_time="2026-08-08T12:00:00Z",
        ),
        Fixture(
            id=103, event=2, team_h=1, team_a=2,
            finished=True, started=True, kickoff_time="2026-08-09T12:00:00Z",
        ),
        Fixture(
            id=104, event=2, team_h=1, team_a=2,
            started=False, kickoff_time="2026-08-10T12:00:00Z",
        ),
        Fixture(
            id=105, event=3, team_h=1, team_a=2,
            started=False, kickoff_time="2026-08-15T12:00:00Z",
        ),
    ])
    return repository


def player_history():
    return [
        {
            "fixture": 101, "round": 1, "total_points": 0, "minutes": 90,
            "kickoff_time": "2026-08-01T12:00:00Z", "opponent_team": 2,
            "was_home": True,
        },
        {
            "fixture": 102, "round": 2, "total_points": 2, "minutes": 60,
            "expected_goals": "0.10", "kickoff_time": "2026-08-08T12:00:00Z",
            "opponent_team": 2, "was_home": True,
        },
        {
            "fixture": 103, "round": 2, "total_points": 3, "minutes": 30,
            "expected_goals": "0.20", "kickoff_time": "2026-08-09T12:00:00Z",
            "opponent_team": 2, "was_home": True,
        },
        {
            "fixture": 104, "round": 2, "total_points": 50, "minutes": 90,
            "kickoff_time": "2026-08-10T12:00:00Z", "opponent_team": 2,
            "was_home": True,
        },
        {
            "fixture": 105, "round": 3, "total_points": 99, "minutes": 90,
            "kickoff_time": "2026-08-15T12:00:00Z", "opponent_team": 2,
            "was_home": True,
        },
    ]


def make_pipeline(repository, histories):
    pipeline = FPLSyncPipeline.__new__(FPLSyncPipeline)
    pipeline.repo = repository
    pipeline.client = MappingHistoryClient(histories)
    return pipeline


def test_backfill_covers_all_players_aggregates_double_gameweeks_and_filters_unfinished(
    tmp_path, monkeypatch
):
    repository = create_repository(tmp_path / "history.db", player_count=32)
    histories = {player_id: {"history": player_history()} for player_id in range(1, 33)}
    pipeline = make_pipeline(repository, histories)
    monkeypatch.setattr(sync_fpl_module.time, "sleep", lambda _seconds: None)

    result = pipeline.sync_player_history_backfill()

    assert result == {
        "players_attempted": 32,
        "players_succeeded": 32,
        "players_failed": 0,
        "history_records": 64,
    }
    assert len(pipeline.client.calls) == 32
    assert repository.get_player_by_id(32) is not None
    for player_id in (1, 16, 32):
        rows = repository.get_player_gameweek_history(player_id)
        assert [(row.round, row.total_points) for row in rows] == [(1, 0), (2, 5)]
        assert rows[0].minutes == 90
        assert rows[1].minutes == 90
        assert rows[1].expected_goals == pytest.approx(0.3)
        assert rows[1].is_provisional is True


def test_empty_history_and_api_failure_preserve_existing_rows_and_resume(tmp_path, monkeypatch):
    repository = create_repository(tmp_path / "resume.db")
    repository.upsert_gameweek_history([
        PlayerGameweekHistory(player_id=1, round=1, total_points=7),
        PlayerGameweekHistory(player_id=2, round=1, total_points=8),
    ])
    pipeline = make_pipeline(repository, {
        1: {"history": []},
        2: RuntimeError("upstream unavailable"),
    })
    monkeypatch.setattr(sync_fpl_module.time, "sleep", lambda _seconds: None)

    first_result = pipeline.sync_player_history_backfill()

    assert first_result["players_succeeded"] == 1
    assert first_result["players_failed"] == 1
    assert [(row.round, row.total_points) for row in repository.get_player_gameweek_history(1)] == [(1, 7)]
    assert [(row.round, row.total_points) for row in repository.get_player_gameweek_history(2)] == [(1, 8)]

    pipeline.client.histories[2] = {"history": player_history()}
    second_result = pipeline.sync_player_history_backfill()

    assert second_result["players_attempted"] == 1
    assert pipeline.client.calls == [1, 2, 2]
    assert second_result["players_succeeded"] == 1


def test_regular_history_sync_rotates_beyond_the_first_player_batch(tmp_path, monkeypatch):
    repository = create_repository(tmp_path / "rotation.db", player_count=32)
    pipeline = make_pipeline(repository, {
        player_id: {"history": []} for player_id in range(1, 33)
    })
    monkeypatch.setattr(sync_fpl_module.time, "sleep", lambda _seconds: None)

    pipeline._sync_top_players_history(limit=30, force_refresh=False)
    first_batch = pipeline.client.calls.copy()
    pipeline._sync_top_players_history(limit=30, force_refresh=False)

    assert first_batch == list(range(1, 31))
    assert pipeline.client.calls[30:32] == [31, 32]
    assert len(pipeline.client.calls) == 60
