"""
Unit tests for SQLite Repository and Data Access Layer.
"""

import pytest
from pathlib import Path
from src.database.connection import init_db
from src.database.models import Team, Player
from src.database.repository import FPLRepository


@pytest.fixture
def temp_repo(tmp_path: Path):
    db_file = tmp_path / "test_fpl.db"
    init_db(db_file)
    return FPLRepository(db_path=db_file)


def test_repository_teams_crud(temp_repo):
    """Test inserting and retrieving teams."""
    teams = [
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Liverpool", short_name="LIV"),
    ]
    temp_repo.upsert_teams(teams)
    fetched = temp_repo.get_all_teams()
    assert len(fetched) == 2
    assert fetched[0].name == "Arsenal"


def test_repository_players_crud_and_filter(temp_repo):
    """Test upserting players and applying position/price/search filters."""
    teams = [Team(id=1, name="Arsenal", short_name="ARS")]
    temp_repo.upsert_teams(teams)

    players = [
        Player(
            id=101, team_id=1, web_name="Saka", first_name="Bukayo", second_name="Saka",
            position="MID", element_type=3, now_cost=100, form=8.5, total_points=50,
        ),
        Player(
            id=102, team_id=1, web_name="Saliba", first_name="William", second_name="Saliba",
            position="DEF", element_type=2, now_cost=60, form=5.0, total_points=35,
        ),
    ]
    temp_repo.upsert_players(players)

    # Filter by position
    midfielders = temp_repo.get_players(position="MID")
    assert len(midfielders) == 1
    assert midfielders[0].web_name == "Saka"

    # Filter by max price
    budget_players = temp_repo.get_players(max_price=7.0)
    assert len(budget_players) == 1
    assert budget_players[0].web_name == "Saliba"

    # Fuzzy search
    search_res = temp_repo.search_players("sal")
    assert len(search_res) == 1
    assert search_res[0].web_name == "Saliba"


def test_db_stats_tracks_latest_successful_fpl_and_news_sync_separately(temp_repo):
    temp_repo.log_ingestion(
        sync_type="fpl", started_at="t1", completed_at="fpl-ok", status="ok"
    )
    temp_repo.log_ingestion(
        sync_type="fpl", started_at="t2", completed_at="fpl-failed", status="error"
    )
    temp_repo.log_ingestion(
        sync_type="news", started_at="t3", completed_at="news-ok", status="ok"
    )
    temp_repo.log_ingestion(
        sync_type="news", started_at="t4", completed_at="news-partial",
        status="partial", error="one feed unavailable",
    )

    stats = temp_repo.get_db_stats()

    assert stats["last_sync"] == "fpl-ok"
    assert stats["last_news_sync"] == "news-ok"
    assert stats["last_news_attempt_at"] == "news-partial"
    assert stats["last_news_status"] == "partial"
