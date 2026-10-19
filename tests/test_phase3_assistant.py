"""Mock-only tests for Phase 3 query routing and grounded assistant orchestration."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.database.models import Fixture, Player
from src.rag.assistant_service import FPLAssistant, _rank_evidence, _response_is_grounded
from src.rag.query_understanding import understand_query


def player(pid, name, team="Arsenal", team_id=1, **kwargs):
    return Player(id=pid, team_id=team_id, web_name=name, first_name="Test", second_name=name,
                  position="MID", element_type=3, now_cost=100, team_name=team,
                  form=6.2, total_points=80, points_per_game=5.1, minutes=900,
                  goals_scored=8, assists=5, expected_goals=5.0, expected_assists=4.0,
                  expected_goal_involvements=9.0, ict_index=90.0, **kwargs)


class FakeRepo:
    def __init__(self, players):
        self.players = players
    def search_players(self, term, limit=20):
        key = term.casefold()
        return [p for p in self.players if key in p.web_name.casefold() or key in p.full_name.casefold()][:limit]
    def get_upcoming_fixtures(self, team_id=None, limit=20):
        return [Fixture(1, 6, team_id or 1, 2, finished=False, kickoff_time="2026-10-10T12:00:00Z", team_h_name="Test FC", team_a_name="Other FC", team_h_short_name="TST", team_a_short_name="OTH", team_h_difficulty=2, team_a_difficulty=4)]
    def get_player_gameweek_history(self, player_id, limit=3):
        return []


def setup_assistant(players=None, docs=None, provider=None):
    players = players or [player(1, "Haaland", "Man City"), player(2, "Gakpo", "Liverpool", 2), player(3, "Saka")]
    repo = FakeRepo(players)
    retriever = MagicMock()
    retriever.repo = repo
    retriever.retrieve.side_effect = lambda query, top_k_news=5, fixture_limit=3: SimpleNamespace(
        target_player=next((p for p in players if p.web_name.casefold() in query.casefold()), None),
            upcoming_fixtures=repo.get_upcoming_fixtures(team_id=1, limit=fixture_limit), player_history=[],
        news_chunks=list(docs or []), formatted_context="",
    )
    llm = provider or MagicMock()
    llm.name = "Offline Mock Reasoner"
    llm.generate_response.return_value = "Grounded offline explanation."
    scoring = MagicMock()
    scoring.evaluate_player.side_effect = lambda p, fx, **kw: SimpleNamespace(final_score=72.0, tier="Strong", positive_reasons=["Form"], negative_reasons=[])
    transfer = MagicMock()
    transfer.evaluate.return_value = SimpleNamespace(
        player_out=players[0], player_in=players[1] if len(players) > 1 else players[0], cost_diff=-1.0, is_affordable=True,
        score_out=SimpleNamespace(final_score=60.0), score_in=SimpleNamespace(final_score=72.0),
        confidence_delta=12.0, verdict="Favorable Move",
    )
    captains = MagicMock()
    captains.get_top_captains.return_value = []
    return FPLAssistant(retriever, llm, scoring, transfer, captains), retriever, scoring, transfer, captains


@pytest.mark.parametrize(("question", "intent", "count"), [
    ("Haaland stats", "general_fpl", 1),
    ("Compare Saka and Gakpo for next 3 gameweeks", "comparison_analysis", 2),
    ("Should I transfer Haaland out for Gakpo?", "transfer_analysis", 2),
    ("Should I captain Haaland this week?", "captain_analysis", 1),
    ("What is the latest injury status of Havertz?", "injury_analysis", 0),
    ("What are Haaland's next fixtures?", "fixture_analysis", 1),
])
def test_query_understanding_routes_common_intents(question, intent, count):
    players = [player(1, "Haaland"), player(2, "Gakpo", "Liverpool", 2), player(3, "Saka")]
    if "Havertz" in question:
        players.append(player(3, "Havertz"))
        count = 1
    parsed = understand_query(question, FakeRepo(players))
    assert parsed.intent == intent
    assert len(parsed.players) == count
    if intent == "transfer_analysis":
        assert parsed.player_out.web_name == "Haaland"
        assert parsed.player_in.web_name == "Gakpo"


def test_transfer_analysis_uses_both_players_fixtures_and_python_score():
    assistant, retriever, scoring, transfer, _ = setup_assistant()
    result = assistant.analyze("Should I transfer Haaland out for Gakpo?")
    assert result.transfer.verdict == "Favorable Move"
    assert result.transfer.confidence_delta == 12.0
    assert result.transfer.player_out.web_name == "Haaland"
    assert result.transfer.player_in.web_name == "Gakpo"
    transfer.evaluate.assert_called_once()
    assert retriever.retrieve.call_count == 2
    assert "DETERMINISTIC SCORE" in result.grounded_context


def test_three_gameweek_comparison_retrieves_both_players_and_fixtures():
    assistant, retriever, scoring, _, _ = setup_assistant()
    result = assistant.analyze("Who is a better option for the next 3 gameweeks, Saka or Gakpo?")
    assert result.parsed.intent == "comparison_analysis"
    assert {p.web_name for p in result.parsed.players} == {"Saka", "Gakpo"}
    assert retriever.retrieve.call_count == 2
    assert set(result.scores) >= {2, 3}
    assert all(ctx.upcoming_fixtures for ctx in result.player_contexts)
    assert scoring.evaluate_player.call_count == 2


def test_captain_query_uses_existing_captain_engine():
    assistant, _, _, _, picker = setup_assistant()
    assistant.analyze("Who should I captain this gameweek?")
    assert picker.get_top_captains.call_count == 2
    picker.get_top_captains.assert_called_with(top_n=5, negative_news_player_ids=set())


def test_injury_query_preserves_current_status_and_unknown_date():
    injured = player(3, "Havertz", status="d", chance_of_playing_next_round=50, news="Hamstring issue")
    assistant, _, _, _, _ = setup_assistant([injured], docs=[])
    result = assistant.analyze("Latest injury situation for Havertz")
    assert result.injury["availability"] == "Doubtful (50% chance)"
    assert "Not publicly available" in result.grounded_context
    assert result.injury["reconciliation"] is None


def test_hybrid_sqlite_and_chroma_evidence_and_source_attribution():
    doc = {"id": "doc1", "text": "Haaland trained today.", "similarity": .9,
           "metadata": {"source": "BBC Sport", "published_at": "2026-09-30", "player": "Haaland", "link": "https://bbc.example/story", "category": "press_conference"}}
    assistant, retriever, _, _, _ = setup_assistant(docs=[doc])
    result = assistant.analyze("Haaland fitness update")
    assert result.player_contexts[0].target_player.web_name == "Haaland"
    assert result.evidence[0]["metadata"]["source"] == "BBC Sport"
    assert "https://bbc.example/story" in result.grounded_context
    retriever.retrieve.assert_called_once()


def test_freshness_ranking_prefers_recent_when_otherwise_relevant():
    old = {"id": "old", "similarity": .9, "metadata": {"published_at": "2026-01-01", "player": "Saka"}}
    new = {"id": "new", "similarity": .5, "metadata": {"published_at": date.today().isoformat(), "player": "Saka"}}
    assert _rank_evidence([old, new])[0]["id"] == "new"


def test_missing_news_is_explicit_and_never_substituted():
    assistant, _, _, _, _ = setup_assistant()
    result = assistant.analyze("Haaland form")
    assert "No relevant recent news or injury evidence was found." in result.grounded_context
    assert "No relevant recent news or injury evidence was found." in result.answer
    assert result.evidence == []


def test_gemini_failure_falls_back_to_structured_offline_answer():
    provider = MagicMock()
    provider.name = "Google Gemini"
    provider.generate_response.side_effect = RuntimeError("offline")
    assistant, _, _, _, _ = setup_assistant(provider=provider)
    result = assistant.analyze("Haaland stats")
    assert result.fallback is True
    assert "Offline/mock analysis" in result.answer
    assert "72.0/100" in result.answer


def test_deterministic_score_is_only_taken_from_python_scoring_engine():
    assistant, _, scoring, _, _ = setup_assistant()
    result = assistant.analyze("Haaland stats")
    assert result.scores[1].final_score == 72.0
    assert "Decision Confidence Score: 72.0/100" in result.grounded_context
    scoring.evaluate_player.assert_called_once()


def test_response_validator_rejects_unretrieved_numbers_and_names():
    parsed = understand_query("Haaland stats", FakeRepo([player(1, "Haaland")]))
    context = "Name: Erling Haaland (Haaland)\nForm: 6.2\nDecision Confidence Score: 72/100"
    assert _response_is_grounded("Haaland form is 6.2.", context, parsed)
    assert not _response_is_grounded("Haaland form is 9.9.", context, parsed)
    assert not _response_is_grounded("Salah form is 6.2.", context, parsed)
    assert not _response_is_grounded("Haaland returns on 14 October 2026.", context, parsed)


def test_followup_transfer_retains_prior_out_player():
    assistant, _, _, transfer, _ = setup_assistant()
    assistant.analyze("What about Gakpo instead?", prior_transfer_out="Haaland")
    transfer.evaluate.assert_called_once()


def test_conflicting_and_stale_evidence_remain_visible_to_context():
    docs = [
        {"id": "a", "text": "Expected back on 14 October 2026.", "similarity": .9, "metadata": {"source": "Arsenal Official", "published_at": "2026-09-30", "player": "Saka", "category": "injury"}},
        {"id": "b", "text": "Expected back on 21 October 2026.", "similarity": .8, "metadata": {"source": "BBC Sport", "published_at": "2026-09-29", "player": "Saka", "category": "injury"}},
    ]
    assistant, _, _, _, _ = setup_assistant([player(3, "Saka")], docs)
    result = assistant.analyze("Saka injury")
    assert len(result.evidence) == 2
    assert "Sources disagree" in result.grounded_context
    assert "14 October 2026" in result.grounded_context
    assert "21 October 2026" in result.grounded_context
    assert all(c.get("metadata", {}).get("published_at") for c in result.evidence)


def test_ambiguous_player_name_is_not_guessed():
    one = player(1, "Smith", team="A")
    two = player(2, "Smith", team="B", team_id=2)
    parsed = understand_query("Smith injury", FakeRepo([one, two]))
    assert len(parsed.players) == 0
    assert parsed.ambiguous
