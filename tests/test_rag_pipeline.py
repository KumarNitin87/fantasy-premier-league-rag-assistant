"""
Comprehensive Automated Tests for Phase 3:
- FootballNewsChunker (overlap, domain metadata tagging, categories, severities, deterministic IDs)
- ChromaVectorStore (idempotent upsert, semantic similarity, metadata filtering)
- RSSNewsClient (resilient fetching, feed failure tolerance, deduplication)
- HybridRetriever (multi-token player recognition, joining structured stats + fixtures + vector news)
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.rag.chunker import FootballNewsChunker
from src.api.rss_client import RSSNewsClient
from src.rag.retriever import HybridRetriever, RetrievalContext
from src.database.models import Player, Fixture, PlayerGameweekHistory


@pytest.fixture
def test_vector_store(tmp_path: Path):
    persist_dir = tmp_path / "test_chroma"
    # Keep this real integration fixture isolated from tests that mock retrieval.
    # A host policy may deny ChromaDB's native dependency; report that as skipped,
    # never substitute a fake vector store for these integration tests.
    try:
        import chromadb  # noqa: F401
    except ImportError as exc:
        message = str(exc)
        if "Application Control policy has blocked" in message and "orjson" in message:
            pytest.skip(f"Windows Application Control blocked ChromaDB's orjson DLL: {message}")
        raise
    from src.rag.vector_store import ChromaVectorStore
    return ChromaVectorStore(persist_dir=persist_dir, collection_name="test_football_news")


@pytest.fixture
def sample_player():
    return Player(
        id=10,
        team_id=1,
        web_name="Saka",
        first_name="Bukayo",
        second_name="Saka",
        position="MID",
        element_type=3,
        now_cost=100,
        code=223340,
        photo_url="https://resources.premierleague.com/premierleague/photos/players/250x250/p223340.png",
        selected_by_percent=35.5,
        status="d",
        chance_of_playing_next_round=75,
        news="Hamstring strain - 75% chance of playing",
        news_added="2026-09-24T10:00:00Z",
        form=7.2,
        points_per_game=6.5,
        total_points=45,
        minutes=450,
        goals_scored=3,
        assists=2,
        clean_sheets=2,
        goals_conceded=3,
        own_goals=0,
        penalties_saved=0,
        penalties_missed=0,
        yellow_cards=1,
        red_cards=0,
        saves=0,
        bonus=6,
        bps=120,
        influence=150.0,
        creativity=180.0,
        threat=140.0,
        ict_index=47.0,
        expected_goals=2.5,
        expected_assists=1.8,
        expected_goal_involvements=4.3,
        expected_goals_conceded=3.2,
    )


# =============================================================================
# 1. Chunker & Metadata Tagging Tests
# =============================================================================

def test_chunker_metadata_tagging():
    """Verify that chunker accurately extracts player, team, category, severity, and timestamp."""
    chunker = FootballNewsChunker(chunk_size=300, chunk_overlap=40)
    title = "Arsenal Fitness Alert: Bukayo Saka Hamstring Concern"
    content = "Mikel Arteta confirmed Bukayo Saka suffered a slight hamstring knock and faces a late fitness test."

    chunks = chunker.chunk_article(
        title=title,
        content=content,
        source="BBC Sport",
        published_at="2026-09-24T14:30:00Z",
        link="https://bbc.com/sport/football/123",
        known_players=["Bukayo Saka", "Erling Haaland"],
        known_teams=["Arsenal", "Manchester City"],
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    meta = chunk["metadata"]

    assert meta["source"] == "BBC Sport"
    assert meta["published_at"] == "2026-09-24T14:30:00Z"
    assert meta["timestamp"] == "2026-09-24T14:30:00Z"
    assert meta["category"] == "injury"
    assert meta["severity"] == "doubtful"
    assert meta["player"] == "Bukayo Saka"
    assert meta["team"] == "Arsenal"
    assert "Bukayo Saka" in meta["player_tags"]


def test_chunker_severities_and_categories():
    """Verify severity and category classifications across various football news patterns."""
    chunker = FootballNewsChunker()

    # Out test
    chunks_out = chunker.chunk_article(
        title="Rodri Knee Surgery",
        content="Rodri has been ruled out for months after undergoing surgery on his torn ACL.",
        source="Sky Sports",
        published_at="2026-09-23T10:00:00Z",
    )
    assert chunks_out[0]["metadata"]["severity"] == "out"
    assert chunks_out[0]["metadata"]["category"] == "injury"

    # Suspended test
    chunks_susp = chunker.chunk_article(
        title="Red Card Appeal Dismissed",
        content="The midfielder received a red card ban and will serve a 3-match suspension.",
        source="Premier League",
        published_at="2026-09-22T09:00:00Z",
    )
    assert chunks_susp[0]["metadata"]["severity"] == "suspended"

    # Fit test
    chunks_fit = chunker.chunk_article(
        title="Haaland Training Update",
        content="Pep Guardiola confirmed the striker has returned to training and is fully fit to start.",
        source="BBC Sport",
        published_at="2026-09-25T11:00:00Z",
    )
    assert chunks_fit[0]["metadata"]["severity"] == "fit"
    assert chunks_fit[0]["metadata"]["category"] == "press_conference"


def test_chunker_deterministic_id_deduplication():
    """Verify that identical content generates identical chunk IDs across multiple runs."""
    chunker = FootballNewsChunker()
    article = {
        "title": "Palmer Signs Contract Extension",
        "content": "Cole Palmer has signed a new long-term deal at Chelsea.",
        "source": "The Guardian",
        "published_at": "2026-09-20T12:00:00Z",
    }

    run1 = chunker.chunk_article(**article)
    run2 = chunker.chunk_article(**article)

    assert len(run1) == len(run2) == 1
    assert run1[0]["id"] == run2[0]["id"]


# =============================================================================
# 2. ChromaDB Vector Store & Idempotency Tests
# =============================================================================

@pytest.mark.integration
def test_vector_store_idempotent_upsert(test_vector_store):
    """Verify that upserting the exact same chunks twice does not create duplicate vectors."""
    chunker = FootballNewsChunker()
    chunks = chunker.chunk_article(
        title="Medical Bulletin: Martin Odegaard",
        content="Martin Odegaard is progressing well after his ankle injury and should return soon.",
        source="Arsenal Medical",
        published_at="2026-09-24T12:00:00Z",
    )

    # First upsert
    added_1 = test_vector_store.add_chunks(chunks)
    assert added_1 == 1
    assert test_vector_store.count() == 1

    # Second upsert (exact same chunks)
    added_2 = test_vector_store.add_chunks(chunks)
    assert added_2 == 1
    assert test_vector_store.count() == 1  # MUST remain 1 (idempotent, no duplicates)


@pytest.mark.integration
def test_vector_store_query_and_metadata_filter(test_vector_store):
    """Verify semantic similarity retrieval and metadata filtering."""
    chunker = FootballNewsChunker()
    chunks_injury = chunker.chunk_article(
        title="Saka Ankle Knock",
        content="Bukayo Saka took a heavy knock to his ankle in training.",
        source="BBC Sport",
        published_at="2026-09-24T10:00:00Z",
    )
    chunks_transfer = chunker.chunk_article(
        title="Striker Transfer Agreement",
        content="Chelsea have completed the transfer agreement for a new forward.",
        source="Sky Sports",
        published_at="2026-09-24T11:00:00Z",
    )

    test_vector_store.add_chunks(chunks_injury + chunks_transfer)
    assert test_vector_store.count() == 2

    # Query with semantic matching
    results = test_vector_store.query("Saka injury news", n_results=2)
    assert len(results) >= 1
    assert "Saka" in results[0]["text"]
    assert results[0]["similarity"] > 0.3

    # Query with metadata filter
    injury_only = test_vector_store.query("football", n_results=5, where_filter={"category": "injury"})
    for item in injury_only:
        assert item["metadata"]["category"] == "injury"


# =============================================================================
# 3. RSS Client Resilience & Deduplication Tests
# =============================================================================

def test_rss_client_resilience_and_deduplication():
    """Verify that RSSNewsClient continues across failed feeds and deduplicates articles."""
    client = RSSNewsClient(feeds={
        "Good Feed": "http://example.com/good_rss",
        "Broken Feed": "http://example.com/broken_rss",
    })

    good_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
        <channel>
            <title>Test Football</title>
            <item>
                <title>Haaland Scores Hat-Trick</title>
                <link>https://example.com/haaland-1</link>
                <description>Erling Haaland scored three goals in City win.</description>
                <pubDate>Thu, 24 Sep 2026 15:00:00 GMT</pubDate>
            </item>
            <item>
                <title>Haaland Scores Hat-Trick</title>
                <link>https://example.com/haaland-1</link>
                <description>Duplicate story with same link.</description>
                <pubDate>Thu, 24 Sep 2026 15:05:00 GMT</pubDate>
            </item>
        </channel>
    </rss>
    """

    def mock_fetch(url):
        if "good" in url:
            return good_xml
        raise ConnectionError("Simulated network timeout")

    with patch.object(client, "_fetch_feed_content", side_effect=mock_fetch):
        articles = client.fetch_all_articles(max_per_feed=10)

    # Should have parsed exactly 1 unique article (second item deduplicated, broken feed ignored)
    assert len(articles) == 1
    assert articles[0]["title"] == "Haaland Scores Hat-Trick"
    assert articles[0]["source"] == "Good Feed"


# =============================================================================
# 4. Hybrid Retriever Tests
# =============================================================================

def test_hybrid_retriever_combines_structured_and_semantic_data(sample_player):
    """Verify that HybridRetriever merges structured SQLite stats + fixtures with Chroma news."""
    mock_repo = MagicMock()
    mock_repo.search_players.return_value = [sample_player]
    mock_repo.get_upcoming_fixtures.return_value = [
        Fixture(
            id=101,
            event=6,
            team_h=1,
            team_a=2,
            team_h_score=None,
            team_a_score=None,
            team_h_difficulty=2,
            team_a_difficulty=4,
            finished=False,
            kickoff_time="2026-09-28T14:00:00Z",
            team_h_name="Arsenal",
            team_h_short_name="ARS",
            team_a_name="Aston Villa",
            team_a_short_name="AVL",
        )
    ]
    mock_repo.get_player_gameweek_history.return_value = [
        PlayerGameweekHistory(
            id=1,
            player_id=10,
            round=5,
            total_points=8,
            minutes=90,
            goals_scored=1,
            assists=0,
            clean_sheets=1,
            goals_conceded=0,
            yellow_cards=0,
            red_cards=0,
            saves=0,
            bonus=2,
            bps=28,
            ict_index=12.4,
            value=100,
            expected_goals=0.6,
            expected_assists=0.3,
            expected_goal_involvements=0.9,
            expected_goals_conceded=0.4,
            kickoff_time="2026-09-21T14:00:00Z",
        )
    ]

    mock_vector_store = MagicMock()
    mock_vector_store.query.return_value = [
        {
            "id": "chunk_saka_01",
            "text": "Bukayo Saka trained with the first team today and looks set to be available.",
            "metadata": {
                "source": "BBC Sport",
                "published_at": "2026-09-25",
                "category": "press_conference",
                "severity": "fit",
            },
            "similarity": 0.88,
        }
    ]

    retriever = HybridRetriever(vector_store=mock_vector_store, repository=mock_repo)
    ctx: RetrievalContext = retriever.retrieve("Will Bukayo Saka start this weekend?")

    assert ctx.target_player is not None
    assert ctx.target_player.web_name == "Saka"
    assert len(ctx.upcoming_fixtures) == 1
    assert len(ctx.player_history) == 1
    assert len(ctx.news_chunks) == 1
    assert ctx.structured_summary["cost"] == 10.0
    assert ctx.structured_summary["status"] == "d"

    # Context string should contain both structured data and news
    assert "Saka" in ctx.formatted_context
    assert "GW6" in ctx.formatted_context
    assert "Aston Villa" in ctx.formatted_context
    assert "trained with the first team" in ctx.formatted_context
