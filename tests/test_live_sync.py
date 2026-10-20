import pyarrow as pa
from datetime import datetime, timedelta, timezone

from src.database.connection import init_db
from src.database.models import Fixture, Player, Team
from src.database.repository import FPLRepository
from src.sync.auto_scheduler import (
    FPLAutoScheduler,
    MATCHDAY_INTERVAL_SECONDS,
    NON_MATCHDAY_INTERVAL_SECONDS,
    detect_matchday,
)
from ui.views.comparison_view import _comparison_dataframe
from ui.views.fixtures_view import _fixture_score, _fixture_status


def test_match_status_and_score_use_source_fields():
    scheduled = Fixture(id=1, event=1, team_h=1, team_a=2)
    live = Fixture(id=2, event=1, team_h=1, team_a=2, started=True, team_h_score=1, team_a_score=0)
    provisional = Fixture(
        id=3, event=1, team_h=1, team_a=2, finished_provisional=True,
        team_h_score=2, team_a_score=1,
    )
    final = Fixture(
        id=4, event=1, team_h=1, team_a=2, finished=True,
        team_h_score=2, team_a_score=1,
    )

    assert _fixture_status(scheduled) == "Scheduled"
    assert _fixture_status(live) == "Live"
    assert _fixture_status(provisional) == "Finished · FPL points provisional"
    assert _fixture_status(final) == "Final"
    assert _fixture_score(live) == "1 - 0"
    assert _fixture_score(scheduled) == "vs"


def test_comparison_table_serializes_mixed_metrics_as_strings():
    dataframe = _comparison_dataframe([
        {"Metric": "Club", "Player A": "Arsenal", "Player B": "Chelsea"},
        {"Metric": "Form", "Player A": 4.2, "Player B": 5},
    ]).set_index("Metric")

    assert dataframe.loc["Form", "Player A"] == "4.2"
    assert all(dataframe[column].map(lambda value: isinstance(value, str)).all() for column in dataframe)
    pa.Table.from_pandas(dataframe)


def test_sync_persists_current_event_points_and_fixture_status(tmp_path):
    database = tmp_path / "live.db"
    init_db(database)
    repo = FPLRepository(db_path=database)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    repo.upsert_players([
        Player(
            id=10, team_id=1, web_name="Saka", first_name="Bukayo",
            second_name="Saka", position="MID", element_type=3,
            now_cost=100, event_points=9, total_points=42,
        )
    ])
    repo.upsert_fixtures([
        Fixture(
            id=20, event=1, team_h=1, team_a=2,
            team_h_score=1, team_a_score=0, started=True,
            finished_provisional=True,
        )
    ])

    player = repo.get_player_by_id(10)
    fixture = repo.get_fixtures_by_gameweek(1)[0]
    assert player.event_points == 9
    assert fixture.started is True
    assert fixture.finished_provisional is True


def test_matchday_detector_recognizes_started_fixture_even_if_not_today(tmp_path):
    database = tmp_path / "matchday.db"
    init_db(database)
    repo = FPLRepository(db_path=database)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    repo.upsert_fixtures([
        Fixture(id=1, event=1, team_h=1, team_a=2, started=True, kickoff_time="2020-01-01T12:00:00Z")
    ])

    assert detect_matchday(repo) is True


def test_matchday_detector_keeps_polling_provisional_points_until_finalized(tmp_path):
    database = tmp_path / "provisional.db"
    init_db(database)
    repo = FPLRepository(db_path=database)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    repo.upsert_fixtures([
        Fixture(
            id=1, event=1, team_h=1, team_a=2, started=True, finished=True,
            finished_provisional=True,
            kickoff_time=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        )
    ])

    assert detect_matchday(repo) is True


def test_old_provisional_fixtures_do_not_keep_fifteen_minute_polling_forever(tmp_path):
    database = tmp_path / "stale-provisional.db"
    init_db(database)
    repo = FPLRepository(db_path=database)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    repo.upsert_fixtures([
        Fixture(
            id=1, event=1, team_h=1, team_a=2, finished=True,
            finished_provisional=True, kickoff_time="2020-01-01T12:00:00Z",
        )
    ])

    assert detect_matchday(repo) is False


def test_scheduler_intervals_and_manual_sync_guard():
    calls = []
    scheduler = FPLAutoScheduler(
        sync_callback=lambda: calls.append("sync") or {"players": 1, "fixtures": 2},
        matchday_detector=lambda: True,
        last_sync_provider=lambda: None,
    )
    assert MATCHDAY_INTERVAL_SECONDS == 15 * 60
    assert NON_MATCHDAY_INTERVAL_SECONDS == 6 * 60 * 60

    assert scheduler.run_sync_now() is True
    assert calls == ["sync"]
    assert scheduler.status["last_sync_at"]
    assert scheduler.status["last_attempt_at"]

    scheduler._lock.acquire()
    try:
        assert scheduler.run_sync_now() is False
    finally:
        scheduler._lock.release()
    assert calls == ["sync"]


def test_scheduler_records_and_propagates_manual_sync_errors():
    scheduler = FPLAutoScheduler(
        sync_callback=lambda: (_ for _ in ()).throw(RuntimeError("upstream unavailable")),
        matchday_detector=lambda: False,
        last_sync_provider=lambda: None,
    )

    try:
        scheduler.run_sync_now()
    except RuntimeError as exc:
        assert str(exc) == "upstream unavailable"
    else:
        raise AssertionError("manual sync should surface the upstream error")

    assert scheduler.status["last_error"] == "upstream unavailable"
    assert scheduler.status["last_attempt_at"]
    assert scheduler.status["last_sync_at"] is None


def test_scheduler_success_callback_error_does_not_misreport_sync_failure():
    scheduler = FPLAutoScheduler(
        sync_callback=lambda: {"players": 1, "fixtures": 1},
        matchday_detector=lambda: False,
        last_sync_provider=lambda: None,
        on_sync_success=lambda _result: (_ for _ in ()).throw(RuntimeError("cache clear failed")),
    )

    assert scheduler.run_sync_now() is True
    assert scheduler.status["last_sync_at"]
    assert scheduler.status["last_error"] is None
    assert scheduler.status["last_callback_error"] == "cache clear failed"


def test_news_scheduler_runs_due_startup_sync_and_records_separate_status():
    calls = []
    scheduler_holder = {}

    def sync_news():
        calls.append("news")
        scheduler_holder["scheduler"].stop()
        return {"status": "ok", "indexed_chunks": 3}

    scheduler = FPLAutoScheduler(
        sync_callback=lambda: {"players": 0, "fixtures": 0},
        matchday_detector=lambda: False,
        last_sync_provider=lambda: datetime.now(timezone.utc).isoformat(),
        news_sync_callback=sync_news,
        last_news_sync_provider=lambda: None,
        news_interval_seconds=6 * 60 * 60,
    )
    scheduler_holder["scheduler"] = scheduler
    scheduler.start()
    scheduler.join(timeout=2)

    assert calls == ["news"]
    assert scheduler.news_status["last_attempt_at"]
    assert scheduler.news_status["last_success_at"]
    assert scheduler.news_status["last_result"]["indexed_chunks"] == 3
    assert scheduler.status["startup_check_complete"] is True


def test_manual_news_scheduler_surfaces_source_failure_without_success_timestamp():
    scheduler = FPLAutoScheduler(
        sync_callback=lambda: {"players": 0, "fixtures": 0},
        matchday_detector=lambda: False,
        last_sync_provider=lambda: None,
        news_sync_callback=lambda: {
            "status": "error",
            "error": "BBC feed unavailable",
            "indexed_chunks": 0,
        },
    )

    assert scheduler.run_news_now() is False
    assert scheduler.news_status["last_attempt_at"]
    assert scheduler.news_status["last_success_at"] is None
    assert scheduler.news_status["last_error"] == "BBC feed unavailable"
