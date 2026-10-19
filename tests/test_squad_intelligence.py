"""Mock-only tests for the Phase 4 squad decision workflow."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.analytics.captain_picker import CaptainPicker
from src.analytics.scoring import DecisionScoringEngine
from src.analytics.squad_intelligence import (
    analyze_player_risk,
    build_fixture_timeline,
    build_squad_report,
    calculate_form_trend,
    evaluate_transfer_plan,
    find_alternatives,
    parse_player_search,
    recommend_starting_xi,
    search_players_natural_language,
    validate_squad,
)
from src.database.connection import init_db
from src.database.models import Fixture, Player, PlayerGameweekHistory, SquadMember, Team
from src.database.repository import FPLRepository


def make_player(pid, pos, team_id, status="a", form=6.0, minutes=900, total=90, name=None):
    name = name or f"P{pid}"
    chance = {"a": 100, "d": 50, "i": 0, "s": 0, "u": 0}[status]
    return Player(id=pid, team_id=team_id, web_name=name, first_name="Test", second_name=name,
                  position=pos, element_type={"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}[pos],
                  now_cost=50 + pid, form=form, total_points=total, points_per_game=4.0,
                  minutes=minutes, goals_scored=8 if pos in {"MID", "FWD"} else 1, assists=4,
                  clean_sheets=5, status=status, chance_of_playing_next_round=chance,
                  expected_goals=5.0, expected_assists=3.0, expected_goal_involvements=8.0,
                  ict_index=80.0, team_name=f"Club {team_id}", team_short_name=f"C{team_id}")


def valid_players():
    roles = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    return [make_player(i + 1, role, (i % 5) + 1, name=f"Player{i+1}") for i, role in enumerate(roles)]


class FakeRepo:
    def __init__(self, players=None):
        self.players = players or valid_players()
        self.teams = [Team(i, f"Club {i}", f"C{i}") for i in range(1, 6)]
        self.fixtures = []
        fid = 1
        for gameweek in range(6, 12):
            for team_id in range(1, 6):
                opponent = team_id % 5 + 1
                self.fixtures.append(Fixture(fid, gameweek, team_id, opponent, team_h_difficulty=(team_id % 5) + 1,
                                             team_a_difficulty=(opponent % 5) + 1, kickoff_time=f"2026-10-{gameweek+3:02d}T12:00:00Z",
                                             team_h_name=f"Club {team_id}", team_a_name=f"Club {opponent}",
                                             team_h_short_name=f"C{team_id}", team_a_short_name=f"C{opponent}"))
                fid += 1
        self.history = {p.id: [SimpleNamespace(round=n, total_points=n + 2, minutes=90 - n * 5) for n in range(1, 6)] for p in self.players}
    def get_upcoming_fixtures(self, team_id=None, limit=20):
        rows = [f for f in self.fixtures if team_id is None or team_id in (f.team_h, f.team_a)]
        return rows[:limit]
    def get_player_gameweek_history(self, player_id, limit=None):
        rows = self.history.get(player_id, [])
        return rows[-limit:] if limit else rows
    def get_players(self, position=None, max_price=None, limit=100, **kwargs):
        rows = [p for p in self.players if (position is None or p.position == position) and (max_price is None or p.cost_float <= max_price)]
        return rows[:limit]
    def get_all_teams(self):
        return self.teams
    def get_my_team(self):
        return []


def test_squad_creation_accepts_required_15_and_position_counts():
    players = valid_players()
    valid, issues = validate_squad(players)
    assert valid and issues == []


@pytest.mark.parametrize("position,expected", [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)])
def test_valid_and_invalid_composition(position, expected):
    players = valid_players()
    assert sum(p.position == position for p in players) == expected
    invalid = [p for p in players if not (p.position == position and next(q for q in players if q.position == position).id == p.id)]
    other_position = next(pos for pos in ("GKP", "DEF", "MID", "FWD") if pos != position)
    invalid.append(make_player(100, other_position, 5, name="Composition Test"))
    valid, issues = validate_squad(invalid)
    assert not valid and issues


def test_squad_persistence_uses_additive_sqlite_tables(tmp_path):
    path = tmp_path / "team.db"
    init_db(path)
    repo = FPLRepository(path)
    repo.upsert_teams([Team(i, f"Club {i}", f"C{i}") for i in range(1, 6)])
    players = valid_players()
    repo.upsert_players(players)
    members = [SquadMember(p.id, p.cost_float, i, i == 10, i == 11) for i, p in enumerate(players)]
    repo.save_my_team(members, 1.5, 2)
    assert len(repo.get_my_team()) == 15
    assert repo.get_my_team_settings() == {"bank_millions": 1.5, "free_transfers": 2}
    assert repo.get_my_team()[10]["is_captain"] == 1


def test_starting_xi_optimizer_returns_valid_formation_and_bench():
    players = valid_players()
    result = recommend_starting_xi(players, FakeRepo(players), DecisionScoringEngine())
    assert len(result.starting) == 11 and len(result.bench) == 4
    defenders, midfielders, forwards = map(int, result.formation.split("-"))
    assert defenders >= 3 and midfielders >= 2 and forwards >= 1
    assert sum(p.position == "GKP" for p in result.starting) == 1
    assert result.bench[0].position == "GKP"


def test_starting_xi_benches_unavailable_player_when_fit_option_exists():
    players = valid_players()
    players[7].status, players[7].chance_of_playing_next_round = "i", 0
    result = recommend_starting_xi(players, FakeRepo(players), DecisionScoringEngine())
    assert players[7] not in result.starting
    assert players[7] in result.bench


def test_captain_and_vice_selection_are_limited_to_my_squad():
    players = valid_players()
    picker = CaptainPicker(FakeRepo(players), DecisionScoringEngine())
    picks = picker.get_top_captains(top_n=2, candidate_players=players)
    assert len(picks) == 2
    assert all(pick.player.id in {p.id for p in players} for pick in picks)
    assert picks[0].player.id != picks[1].player.id


def test_injury_availability_reduces_existing_decision_score():
    player = make_player(20, "MID", 1, status="i")
    engine = DecisionScoringEngine()
    healthy_score = engine.evaluate_player(make_player(20, "MID", 1), []).final_score
    injured_score = engine.evaluate_player(player, []).final_score
    assert injured_score == 0
    assert healthy_score > injured_score


def test_single_transfer_checks_cost_fixtures_and_score_delta():
    squad = valid_players()
    outgoing = squad[7]
    incoming = make_player(30, "MID", outgoing.team_id, name="Alternative")
    repo = FakeRepo(squad + [incoming])
    plan = evaluate_transfer_plan(squad, [(outgoing, incoming)], 5.0, 1, repo, DecisionScoringEngine())
    assert plan.affordable and plan.legal
    assert plan.bank_after == pytest.approx(5.0 - plan.budget_impact)
    assert len(plan.simulations) == 1


def test_multi_transfer_uses_combined_budget_and_squad_legality():
    squad = valid_players()
    incoming_a = make_player(30, "MID", 3)
    incoming_b = make_player(31, "DEF", 3)
    plan = evaluate_transfer_plan(squad, [(squad[7], incoming_a), (squad[2], incoming_b)], 10, 2,
                                  FakeRepo(squad + [incoming_a, incoming_b]), DecisionScoringEngine())
    assert plan.legal and plan.affordable
    assert len(plan.simulations) == 2
    assert plan.budget_impact == pytest.approx(incoming_a.cost_float + incoming_b.cost_float - squad[7].cost_float - squad[2].cost_float)


def test_what_if_evaluation_does_not_persist_saved_team():
    squad = valid_players()
    repo = FakeRepo(squad)
    before = [p.id for p in squad]
    evaluate_transfer_plan(squad, [(squad[7], make_player(30, "MID", 4))], 5, 1, repo, DecisionScoringEngine())
    assert [p.id for p in squad] == before


def test_alternatives_filter_position_budget_and_current_squad():
    squad = valid_players()
    target = make_player(30, "MID", 3, name="Affordable Mid")
    too_costly = make_player(31, "MID", 5, name="Too Costly")
    too_costly.now_cost = 200
    other_position = make_player(32, "DEF", 4, name="Defender")
    repo = FakeRepo(squad + [target, too_costly, other_position])
    alternatives = find_alternatives(squad[7], squad, repo, DecisionScoringEngine(), 5, limit=8)
    assert all(p.position == "MID" and p.id not in {x.id for x in squad} for p, _ in alternatives)
    assert too_costly not in [p for p, _ in alternatives]


def test_natural_language_player_filters_apply_sqlite_fields():
    players = valid_players() + [make_player(30, "MID", 4, form=7.0, name="Affordable Mid")]
    repo = FakeRepo(players)
    filters = parse_player_search("Find midfielders under £8m with good fixtures and no major injury concerns")
    assert filters.position == "MID" and filters.max_price == 8.0 and filters.good_fixtures and filters.available_only
    results = search_players_natural_language("Find midfielders under £8m with good fixtures and no major injury concerns", repo, DecisionScoringEngine())
    assert all(row["player"].position == "MID" and row["player"].cost_float < 8 for row in results)


def test_last_five_form_trend_uses_only_recorded_history():
    player = make_player(1, "MID", 1)
    history = [SimpleNamespace(round=i, total_points=i, minutes=i * 10) for i in range(1, 7)]
    trend = calculate_form_trend(player, history)
    assert trend.rounds == [2, 3, 4, 5, 6]
    assert trend.points == [2, 3, 4, 5, 6]
    assert trend.average_points == 4.0
    assert trend.direction == "up"


def test_fixture_timeline_uses_existing_home_away_and_fdr():
    player = make_player(1, "MID", 1)
    repo = FakeRepo([player])
    timeline = build_fixture_timeline(player, repo.get_upcoming_fixtures(1, 5), repo.get_all_teams(), 3)
    assert len(timeline) == 3
    assert {"gameweek", "opponent", "home", "fdr", "kickoff"}.issubset(timeline[0])


def test_risk_radar_uses_explicit_injury_and_rotation_evidence():
    player = make_player(1, "MID", 1, status="d")
    history = [SimpleNamespace(round=i, minutes=30, total_points=0) for i in range(1, 6)]
    risk = analyze_player_risk(player, FakeRepo([player]).get_upcoming_fixtures(1, 5), history, [{"metadata": {"severity": "doubtful"}}])
    assert risk.levels["Injury"] == "Medium"
    assert risk.levels["Rotation"] == "High"
    assert "minutes" in risk.reasons["Rotation"]


def test_gameweek_analysis_combines_rag_scores_xi_risks_and_fallback():
    players = valid_players()
    repo = FakeRepo(players)
    doc = {"id": "news1", "text": "Player1 had a minor fitness concern.", "similarity": .85,
           "metadata": {"player": "Player1", "source": "BBC Sport", "published_at": "2026-10-01", "category": "injury", "severity": "doubtful", "link": "https://bbc.example/news"}}
    vector = MagicMock()
    vector.query.return_value = [doc]
    picker = CaptainPicker(repo, DecisionScoringEngine())
    llm = MagicMock()
    llm.generate_response.side_effect = RuntimeError("offline")
    report = build_squad_report(players, repo, DecisionScoringEngine(), picker, vector, llm)
    assert len(report.xi.starting) == 11
    assert len(report.captains) == 2
    assert report.evidence
    assert report.health_score <= 100
    assert report.explanation.startswith("Offline mode")


def test_report_explanation_rejects_unsupported_numeric_claims():
    players = valid_players()
    llm = MagicMock()
    llm.generate_response.return_value = "Your squad has 987 players."
    report = build_squad_report(players, FakeRepo(players), DecisionScoringEngine(), CaptainPicker(FakeRepo(players), DecisionScoringEngine()), llm_provider=llm)
    assert "unsupported numeric information" in report.explanation


def test_injury_return_info_is_retained_from_source_evidence():
    players = valid_players()
    player = players[9]
    player.status = "i"
    doc = {"id": "injury", "text": "Expected back on 14 October 2026 after a hamstring injury.", "similarity": .9,
           "metadata": {"player": player.web_name, "source": "Club Official", "published_at": "2026-10-01", "category": "injury", "link": "https://club.example/player"}}
    vector = MagicMock()
    vector.query.side_effect = lambda q, n_results=2: [doc] if player.web_name in q else []
    report = build_squad_report(players, FakeRepo(players), DecisionScoringEngine(), CaptainPicker(FakeRepo(players), DecisionScoringEngine()), vector)
    assert player.id in report.injury_profiles
    info = report.injury_profiles[player.id]["reconciliation"]["info"]
    assert str(info["expected_return_date"]) == "2026-10-14"
