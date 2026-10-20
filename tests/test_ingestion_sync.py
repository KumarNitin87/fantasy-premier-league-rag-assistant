"""Mocked status-recording tests for FPL and news ingestion pipelines."""

from pathlib import Path

import pytest

from src.ingestion import sync_fpl as sync_fpl_module
from src.ingestion.sync_fpl import FPLSyncPipeline
from src.ingestion.sync_news import NewsIngestionPipeline
from src.api.rss_client import RSSNewsClient
from src.sync.process_lock import acquire_news_sync_lock


class RecordingRepository:
    def __init__(self):
        self.db_path = Path("unused-test-database.sqlite")
        self.logs = []

    def log_ingestion(self, **entry):
        self.logs.append(entry)

    def get_players(self, **_kwargs):
        return []

    def get_all_teams(self):
        return []


def make_fpl_pipeline(monkeypatch, tmp_path, fail_at_fixtures=False):
    repository = RecordingRepository()
    repository.db_path = tmp_path / "unused-test-database.sqlite"
    pipeline = FPLSyncPipeline.__new__(FPLSyncPipeline)
    pipeline.repo = repository
    monkeypatch.setattr(sync_fpl_module, "init_db", lambda _path: None)
    pipeline._fetch_or_fallback_bootstrap = lambda: {
        "teams": [{"id": 1}],
        "elements": [{"id": 1}],
    }
    pipeline._process_teams = lambda rows: len(rows)
    pipeline._process_players = lambda rows: len(rows)
    pipeline._fetch_or_fallback_fixtures = lambda: [{"id": 1}]
    if fail_at_fixtures:
        def fail(_rows):
            raise RuntimeError("fixture processing failed")
        pipeline._process_fixtures = fail
    else:
        pipeline._process_fixtures = lambda rows: len(rows)
    return pipeline, repository


def test_fpl_sync_records_success_counts_and_timestamps(monkeypatch, tmp_path):
    pipeline, repository = make_fpl_pipeline(monkeypatch, tmp_path)

    result = pipeline.sync_all(sync_player_history_top_n=0)

    assert result == {"teams": 1, "players": 1, "fixtures": 1, "history_records": 0}
    assert len(repository.logs) == 1
    log = repository.logs[0]
    assert log["sync_type"] == "fpl"
    assert log["status"] == "ok"
    assert log["players"] == log["teams"] == log["fixtures"] == 1
    assert log["started_at"] and log["completed_at"]


def test_fpl_sync_records_failure_without_swallowing_it(monkeypatch, tmp_path):
    pipeline, repository = make_fpl_pipeline(monkeypatch, tmp_path, fail_at_fixtures=True)

    with pytest.raises(RuntimeError, match="fixture processing failed"):
        pipeline.sync_all(sync_player_history_top_n=0)

    assert len(repository.logs) == 1
    log = repository.logs[0]
    assert log["sync_type"] == "fpl"
    assert log["status"] == "error"
    assert log["error"] == "RuntimeError: fixture processing failed"
    assert log["players"] == log["teams"] == 1
    assert log["fixtures"] == 0


class EmptyRSSClient:
    def fetch_all_articles(self, **_kwargs):
        return []


class EmptyVectorStore:
    def __init__(self, fail=False):
        self.fail = fail

    def add_chunks(self, _chunks):
        if self.fail:
            raise RuntimeError("vector upsert failed")
        return 0

    def count(self):
        return 0


def make_news_pipeline(tmp_path, fail=False):
    repository = RecordingRepository()
    repository.db_path = tmp_path / "news-test.db"
    pipeline = NewsIngestionPipeline.__new__(NewsIngestionPipeline)
    pipeline.repo = repository
    pipeline.rss_client = EmptyRSSClient()
    pipeline.vector_store = EmptyVectorStore(fail=fail)
    return pipeline, repository


def test_news_sync_records_successful_completion(tmp_path):
    pipeline, repository = make_news_pipeline(tmp_path)

    result = pipeline.run(max_per_feed=1)

    assert result["indexed_chunks"] == 0
    assert result["total_vectors"] == 0
    assert len(repository.logs) == 1
    assert repository.logs[0]["sync_type"] == "news"
    assert repository.logs[0]["status"] == "ok"
    assert repository.logs[0]["started_at"]
    assert repository.logs[0]["completed_at"]


def test_news_sync_records_failure_and_preserves_exception(tmp_path):
    pipeline, repository = make_news_pipeline(tmp_path, fail=True)

    with pytest.raises(RuntimeError, match="vector upsert failed"):
        pipeline.run(max_per_feed=1)

    assert len(repository.logs) == 1
    assert repository.logs[0]["sync_type"] == "news"
    assert repository.logs[0]["status"] == "error"
    assert repository.logs[0]["error"] == "RuntimeError: vector upsert failed"


def test_news_sync_records_partial_feed_failure_without_claiming_full_success(tmp_path):
    pipeline, repository = make_news_pipeline(tmp_path)
    pipeline.rss_client.last_errors = ["BBC Sport: OSError"]
    pipeline.rss_client.last_successful_feeds = 1

    result = pipeline.run(max_per_feed=1)

    assert result["status"] == "partial"
    assert result["error"] == "BBC Sport: OSError"
    assert repository.logs[0]["status"] == "partial"
    assert repository.logs[0]["error"] == "BBC Sport: OSError"


def test_news_sync_records_all_feed_failures_as_error(tmp_path):
    pipeline, repository = make_news_pipeline(tmp_path)
    pipeline.rss_client.last_errors = ["BBC Sport: OSError"]
    pipeline.rss_client.last_successful_feeds = 0

    result = pipeline.run(max_per_feed=1)

    assert result["status"] == "error"
    assert result["error"] == "BBC Sport: OSError"
    assert repository.logs[0]["status"] == "error"


def test_rss_client_does_not_use_unbounded_parser_network_fallback(monkeypatch):
    client = RSSNewsClient(feeds={"Test Feed": "https://example.test/rss"})

    def fail_fetch(_url):
        client._last_fetch_error = "HTTP 503"
        return None

    client._fetch_feed_content = fail_fetch
    monkeypatch.setattr("src.api.rss_client.feedparser.parse", lambda *_args: pytest.fail("unexpected network fallback"))

    assert client.fetch_all_articles() == []
    assert client.last_errors == ["Test Feed: HTTP 503"]
    assert client.last_successful_feeds == 0


def test_news_sync_preserves_missing_publication_date(tmp_path):
    pipeline, _repository = make_news_pipeline(tmp_path)
    pipeline.rss_client.fetch_all_articles = lambda **_kwargs: [{
        "title": "A dated claim without a date",
        "summary": "Source content",
        "source": "Test Feed",
        "link": "https://example.test/article",
    }]
    captured = []

    class RecordingChunker:
        def chunk_article(self, **kwargs):
            captured.append(kwargs["published_at"])
            return [{"id": "test", "text": kwargs["title"], "metadata": {}}]

    pipeline.chunker = RecordingChunker()

    pipeline.run(max_per_feed=1)

    assert captured == [""]


def test_news_sync_lock_blocks_overlapping_jobs(tmp_path):
    db_path = tmp_path / "lock-test.db"

    with acquire_news_sync_lock(db_path) as first_acquired:
        assert first_acquired is True
        with acquire_news_sync_lock(db_path) as second_acquired:
            assert second_acquired is False


def test_fpl_api_timeout_error_handling(monkeypatch):
    import requests
    from src.api.fpl_client import FPLClient, FPLAPIError
    client = FPLClient()

    def mock_get(*args, **kwargs):
        raise requests.exceptions.Timeout("Connection timed out after 15s")

    monkeypatch.setattr(client.session, "get", mock_get)
    with pytest.raises(FPLAPIError, match="timed out"):
        client.get_bootstrap_static(force_refresh=True)


def test_fpl_api_malformed_response_handling(monkeypatch):
    from src.api.fpl_client import FPLClient, FPLValidationError
    client = FPLClient()

    class FakeResponse:
        status_code = 200
        ok = True
        content = b"{}"

        def json(self):
            return {"teams": "not-a-list", "elements": "not-a-list"}

    monkeypatch.setattr(client.session, "get", lambda *a, **kw: FakeResponse())
    with pytest.raises(FPLValidationError, match="validation failed"):
        client.get_bootstrap_static(force_refresh=True)


def test_fpl_sync_preserves_valid_data_on_api_failure(tmp_path):
    from src.database.connection import init_db
    from src.database.repository import FPLRepository
    from src.database.models import Team, Player, Fixture
    from src.api.fpl_client import FPLAPIError

    db_file = tmp_path / "test_preservation.db"
    init_db(db_file)
    repo = FPLRepository(db_path=db_file)
    repo.upsert_teams([
        Team(id=1, name="Arsenal", short_name="ARS"),
        Team(id=2, name="Chelsea", short_name="CHE"),
    ])
    repo.upsert_players([
        Player(id=1, team_id=1, web_name="Saka", first_name="Bukayo", second_name="Saka",
               position="MID", element_type=3, now_cost=100, total_points=34)
    ])
    repo.upsert_fixtures([
        Fixture(id=1, event=1, team_h=1, team_a=2, finished=True, team_h_score=2, team_a_score=0)
    ])

    pipeline = FPLSyncPipeline(repository=repo)
    pipeline.raw_dir = tmp_path / "raw"
    pipeline.raw_dir.mkdir(parents=True, exist_ok=True)

    def failing_fetch(**_kw):
        raise FPLAPIError("API 503 Service Unavailable")

    pipeline._fetch_or_fallback_bootstrap = failing_fetch

    with pytest.raises(FPLAPIError, match="503 Service Unavailable"):
        pipeline.sync_all(force_refresh=True, allow_fallback=False)

    # Valid records must be completely preserved
    players = repo.get_players()
    assert len(players) == 1
    assert players[0].web_name == "Saka"
    assert players[0].total_points == 34
    assert len(repo.get_all_teams()) == 2
    assert repo.count_fixtures() == 1

    # Accurate last sync and attempt status
    stats = repo.get_db_stats()
    assert stats["last_sync"] == "Never"
    assert stats["last_fpl_status"] == "error"
    assert "FPLAPIError" in stats["last_fpl_error"]


def test_fpl_sync_duplicate_safe_and_updates_statistics(tmp_path):
    from src.database.connection import init_db
    from src.database.repository import FPLRepository
    from src.database.models import Team, Player, Fixture

    db_file = tmp_path / "test_updates.db"
    init_db(db_file)
    repo = FPLRepository(db_path=db_file)
    pipeline = FPLSyncPipeline(repository=repo)
    pipeline.raw_dir = tmp_path / "raw"
    pipeline.raw_dir.mkdir(parents=True, exist_ok=True)

    # First sync run
    pipeline._fetch_or_fallback_bootstrap = lambda **_kw: {
        "teams": [
            {"id": 1, "name": "Arsenal", "short_name": "ARS"},
            {"id": 2, "name": "Chelsea", "short_name": "CHE"},
        ],
        "elements": [{
            "id": 1, "team": 1, "web_name": "Saka", "first_name": "Bukayo", "second_name": "Saka",
            "element_type": 3, "now_cost": 100, "total_points": 34, "form": "4.0", "goals_scored": 3,
            "assists": 2, "minutes": 450, "clean_sheets": 2, "bonus": 5, "bps": 120,
            "status": "a", "chance_of_playing_next_round": 100,
            "event_points": 5,
            "expected_goals": "2.8", "expected_assists": "1.9",
        }],
    }
    pipeline._fetch_or_fallback_fixtures = lambda **_kw: [{
        "id": 1, "event": 6, "team_h": 1, "team_a": 2,
        "team_h_score": 2, "team_a_score": 1, "finished": False,
        "finished_provisional": True, "started": True, "kickoff_time": "2026-10-10T11:30:00Z"
    }]
    pipeline._sync_top_players_history = lambda **_kw: 1

    res1 = pipeline.sync_all(sync_player_history_top_n=1)
    assert res1["players"] == 1
    assert repo.get_players()[0].total_points == 34
    fixture1 = repo.get_fixtures_by_gameweek(6)[0]
    assert fixture1.finished is False
    assert fixture1.finished_provisional is True
    assert fixture1.team_h_score == 2
    assert fixture1.team_a_score == 1

    # Second sync run with updated match points (duplicate-safe in-place update)
    pipeline._fetch_or_fallback_bootstrap = lambda **_kw: {
        "teams": [
            {"id": 1, "name": "Arsenal", "short_name": "ARS"},
            {"id": 2, "name": "Chelsea", "short_name": "CHE"},
        ],
        "elements": [{
            "id": 1, "team": 1, "web_name": "Saka", "first_name": "Bukayo", "second_name": "Saka",
            "element_type": 3, "now_cost": 101, "total_points": 36, "form": "5.0", "goals_scored": 4,
            "assists": 2, "minutes": 540, "clean_sheets": 2, "bonus": 7, "bps": 145,
            "status": "a", "chance_of_playing_next_round": 100,
            "event_points": 13,
            "expected_goals": "3.5", "expected_assists": "2.1",
        }],
    }
    pipeline._fetch_or_fallback_fixtures = lambda **_kw: [{
        "id": 1, "event": 6, "team_h": 1, "team_a": 2,
        "team_h_score": 2, "team_a_score": 1, "finished": True,
        "finished_provisional": True, "started": True, "kickoff_time": "2026-10-10T11:30:00Z"
    }]

    res2 = pipeline.sync_all(sync_player_history_top_n=1)
    assert res2["players"] == 1
    # Check no duplicate player row was created
    all_players = repo.get_players()
    assert len(all_players) == 1
    p = all_players[0]
    assert p.total_points == 36
    assert p.event_points == 13
    assert p.now_cost == 101
    assert p.goals_scored == 4
    assert p.bonus == 7
    assert p.expected_goals == 3.5

    # Check fixture transitioned to final finished
    fixture2 = repo.get_fixtures_by_gameweek(6)[0]
    assert fixture2.finished is True
    assert fixture2.started is True
    assert fixture2.team_h_score == 2

    # Verify ingestion log status
    stats = repo.get_db_stats()
    assert stats["last_sync"] != "Never"
    assert stats["last_fpl_status"] == "ok"


def test_client_cache_invalidation():
    from src.api.fpl_client import FPLClient
    client = FPLClient()
    client._cache.set("test_key", "value1")
    assert client._cache.get("test_key", 300) == "value1"
    client.clear_cache()
    assert client._cache.get("test_key", 300) is None


def test_separate_fpl_and_news_sync_status(tmp_path):
    from src.database.connection import init_db
    from src.database.repository import FPLRepository

    db_file = tmp_path / "test_status.db"
    init_db(db_file)
    repo = FPLRepository(db_path=db_file)

    # Initial state
    stats = repo.get_db_stats()
    assert stats["last_sync"] == "Never"
    assert stats["last_news_sync"] == "Never"
    assert stats["last_fpl_status"] == "Never run"
    assert stats["last_news_status"] == "Never run"

    # Log FPL success and News failure
    repo.log_ingestion(sync_type="fpl", status="ok", started_at="2026-10-11T12:00:00Z", completed_at="2026-10-11T12:01:00Z")
    repo.log_ingestion(sync_type="news", status="error", error="FeedUnavailableError", started_at="2026-10-11T12:02:00Z", completed_at="2026-10-11T12:02:10Z")

    stats = repo.get_db_stats()
    assert stats["last_sync"] == "2026-10-11T12:01:00Z"
    assert stats["last_fpl_status"] == "ok"
    assert stats["last_news_sync"] == "Never"  # Never shows failed sync as successful
    assert stats["last_news_status"] == "error"
    assert stats["last_news_error"] == "FeedUnavailableError"
