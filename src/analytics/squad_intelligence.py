"""Deterministic squad, XI, transfer, form, fixture, and risk analysis for Phase 4."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import re

from src.analytics.captain_picker import CaptainPicker, CaptainSuggestion
from src.analytics.metrics import calculate_avg_fdr
from src.analytics.scoring import DecisionScoringEngine, ScoreBreakdown
from src.analytics.transfer_eval import TransferAnalyzer
from src.database.models import Fixture, Player
from src.rag.injury_intelligence import reconcile_injury_sources


REQUIRED_SQUAD = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
VALID_FORMATIONS = ((3, 4, 3), (3, 5, 2), (4, 4, 2), (4, 3, 3), (4, 5, 1), (5, 2, 3), (5, 3, 2), (5, 4, 1))


@dataclass
class XIRecommendation:
    starting: List[Player]
    bench: List[Player]
    formation: str
    scores: Dict[int, float]
    reasons: Dict[int, List[str]]
    utilities: Dict[int, float] = field(default_factory=dict)


@dataclass
class PlayerRisk:
    player: Player
    levels: Dict[str, str]
    reasons: Dict[str, str]
    overall: str


@dataclass
class TransferPlan:
    moves: List[Tuple[Player, Player]]
    budget_impact: float
    bank_after: float
    affordable: bool
    legal: bool
    legality_issues: List[str]
    score_delta: float
    risk_delta: int
    fixture_delta: float
    verdict: str
    simulations: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class FormTrend:
    player: Player
    rounds: List[int]
    points: List[int]
    minutes: List[int]
    average_points: Optional[float]
    average_minutes: Optional[float]
    direction: str


@dataclass
class SquadReport:
    xi: XIRecommendation
    captains: List[CaptainSuggestion]
    risks: List[PlayerRisk]
    health_score: int
    injury_count: int
    doubtful_count: int
    rotation_count: int
    difficult_fixture_count: int
    transfer_candidates: List[Player]
    alternatives: Dict[int, List[Tuple[Player, float]]]
    trends: List[FormTrend]
    evidence: List[Dict[str, Any]]
    injury_profiles: Dict[int, Dict[str, Any]]
    explanation: str = ""


def validate_squad(players: Sequence[Player]) -> Tuple[bool, List[str]]:
    issues = []
    if len(players) != 15:
        issues.append(f"Select exactly 15 players (currently {len(players)}).")
    ids = [p.id for p in players]
    if len(ids) != len(set(ids)):
        issues.append("Squad contains duplicate players.")
    counts = {pos: sum(p.position == pos for p in players) for pos in REQUIRED_SQUAD}
    for pos, required in REQUIRED_SQUAD.items():
        if counts[pos] != required:
            issues.append(f"Select exactly {required} {pos} players (currently {counts[pos]}).")
    club_counts: Dict[int, int] = {}
    for player in players:
        club_counts[player.team_id] = club_counts.get(player.team_id, 0) + 1
    if any(count > 3 for count in club_counts.values()):
        issues.append("A club may contribute at most 3 players.")
    return not issues, issues


def recommend_starting_xi(
    players: Sequence[Player],
    repository,
    scoring: DecisionScoringEngine,
    negative_news_player_ids: Optional[set] = None,
    manual_bench_order: Optional[Sequence[int]] = None,
) -> XIRecommendation:
    if len(players) != 15:
        raise ValueError("Starting XI optimization requires a complete 15-player squad")
    scores: Dict[int, float] = {}
    reasons: Dict[int, List[str]] = {}
    utilities: Dict[int, float] = {}
    fixtures_by_player: Dict[int, List[Fixture]] = {}
    for player in players:
        fixtures = repository.get_upcoming_fixtures(team_id=player.team_id, limit=5)
        fixtures_by_player[player.id] = fixtures
        breakdown = scoring.evaluate_player(
            player, fixtures,
            has_negative_news=bool(negative_news_player_ids and player.id in negative_news_player_ids),
        )
        scores[player.id] = breakdown.final_score
        reasons[player.id] = list(breakdown.positive_reasons) + list(breakdown.negative_reasons)
        history = repository.get_player_gameweek_history(player.id, limit=5)
        recent_minutes = [item.minutes for item in history[-5:]]
        multiplier = 1.0
        if len(recent_minutes) >= 3 and not any(item.get("factor") == "Minutes / Rotation Risk" for item in breakdown.penalties_applied):
            average_minutes = sum(recent_minutes) / len(recent_minutes)
            multiplier = 0.65 if average_minutes < 45 else 0.80 if average_minutes < 60 else 1.0
            if multiplier < 1.0:
                reasons[player.id].append(f"Recent minutes average {average_minutes:.0f}; XI selection includes a minutes-risk adjustment.")
        xgi90 = (player.expected_goal_involvements / player.minutes * 90) if player.minutes > 0 else 0.0
        utilities[player.id] = round(scores[player.id] * multiplier + min(5.0, xgi90 * 5.0), 3)

    gks = sorted((p for p in players if p.position == "GKP"), key=lambda p: (not _is_unavailable(p), utilities[p.id], p.minutes), reverse=True)
    best = None
    for defenders, midfielders, forwards in VALID_FORMATIONS:
        picked = [gks[0]]
        picked.extend(_top_position(players, "DEF", defenders, utilities))
        picked.extend(_top_position(players, "MID", midfielders, utilities))
        picked.extend(_top_position(players, "FWD", forwards, utilities))
        total = sum(utilities[p.id] for p in picked)
        available = sum(not _is_unavailable(p) for p in picked)
        key = (available, total)
        if best is None or key > best[0]:
            best = (key, picked, (defenders, midfielders, forwards))
    selected_ids = {p.id for p in best[1]}
    bench_pool = [p for p in players if p.id not in selected_ids]
    gk_bench = sorted((p for p in bench_pool if p.position == "GKP"), key=lambda p: scores[p.id], reverse=True)
    outfield_bench = [p for p in bench_pool if p.position != "GKP"]
    if manual_bench_order:
        order = {player_id: idx for idx, player_id in enumerate(manual_bench_order)}
        outfield_bench.sort(key=lambda p: (order.get(p.id, 999), -scores[p.id]))
    else:
        outfield_bench.sort(key=lambda p: (scores[p.id], p.minutes), reverse=True)
    formation = "-".join(str(n) for n in best[2])
    return XIRecommendation(best[1], gk_bench + outfield_bench, formation, scores, reasons, utilities)


def _top_position(players: Sequence[Player], position: str, count: int, scores: Dict[int, float]) -> List[Player]:
    eligible = [p for p in players if p.position == position]
    return sorted(eligible, key=lambda p: (not _is_unavailable(p), scores[p.id], p.minutes), reverse=True)[:count]


def _is_unavailable(player: Player) -> bool:
    return player.status in {"i", "s", "u"} or player.chance_of_playing_next_round == 0


def calculate_form_trend(player: Player, history: Sequence[Any]) -> FormTrend:
    recent = sorted(history, key=lambda row: row.round)[-5:]
    points = [int(row.total_points or 0) for row in recent]
    minutes = [int(row.minutes or 0) for row in recent]
    if len(points) < 2:
        direction = "insufficient data"
    elif points[-1] > points[0]:
        direction = "up"
    elif points[-1] < points[0]:
        direction = "down"
    else:
        direction = "steady"
    return FormTrend(
        player, [int(row.round) for row in recent], points, minutes,
        round(sum(points) / len(points), 2) if points else None,
        round(sum(minutes) / len(minutes), 1) if minutes else None,
        direction,
    )


def analyze_player_risk(player: Player, fixtures: Sequence[Fixture], history: Sequence[Any], evidence: Sequence[Dict[str, Any]] = ()) -> PlayerRisk:
    levels: Dict[str, str] = {}
    reasons: Dict[str, str] = {}
    chance = player.chance_of_playing_next_round
    if player.status in {"i", "s", "u"} or chance == 0:
        levels["Injury"] = "High"
        reasons["Injury"] = f"FPL availability is {player.status.upper()} ({chance if chance is not None else 0}% chance)."
    elif player.status == "d" or (chance is not None and chance < 100):
        levels["Injury"] = "Medium"
        reasons["Injury"] = f"FPL lists {chance}% chance of playing." if chance is not None else "FPL status is doubtful."
    else:
        levels["Injury"] = "Low"
        reasons["Injury"] = "FPL availability is marked available."

    recent = sorted(history, key=lambda row: row.round)[-5:]
    mins = [row.minutes for row in recent]
    if len(mins) >= 3:
        average = sum(mins) / len(mins)
        low_count = sum(value < 60 for value in mins)
        levels["Rotation"] = "High" if average < 45 or low_count >= 4 else "Medium" if average < 70 or low_count >= 2 else "Low"
        reasons["Rotation"] = f"Last {len(mins)} recorded appearances averaged {average:.0f} minutes ({low_count} below 60)."
    else:
        levels["Rotation"] = "Unknown"
        reasons["Rotation"] = "Fewer than 3 recent gameweek records are available."

    avg_fdr = calculate_avg_fdr(list(fixtures), player.team_id, min(5, len(fixtures) or 3))
    levels["Fixture"] = "High" if avg_fdr >= 4 else "Medium" if avg_fdr >= 3.25 else "Low"
    reasons["Fixture"] = f"Upcoming average FDR is {avg_fdr:.2f} across {len(fixtures[:5])} stored fixtures." if fixtures else "No upcoming fixture data is available."

    levels["Form"] = "High" if player.form < 2 else "Medium" if player.form < 4 else "Low"
    reasons["Form"] = f"Current FPL form is {player.form}."

    adverse = [item for item in evidence if str(item.get("metadata", {}).get("severity", "")).casefold() in {"out", "injured", "doubtful"}]
    levels["News"] = "High" if adverse else "Low" if evidence else "Unknown"
    reasons["News"] = f"{len(adverse)} retrieved item(s) carry an adverse availability label." if evidence else "No relevant indexed news evidence was retrieved."

    numeric = [3 if level == "High" else 2 if level == "Medium" else 1 if level == "Low" else 0 for level in levels.values()]
    average = sum(numeric) / max(1, len(numeric))
    overall = "High" if average >= 2.1 or levels["Injury"] == "High" else "Medium" if average >= 1.5 else "Low"
    return PlayerRisk(player, levels, reasons, overall)


def squad_health_score(players: Sequence[Player], risks: Sequence[PlayerRisk]) -> int:
    penalty = 0
    for player in players:
        if player.status in {"i", "s", "u"} or player.chance_of_playing_next_round == 0:
            penalty += 12
        elif player.status == "d" or (player.chance_of_playing_next_round is not None and player.chance_of_playing_next_round < 100):
            penalty += 6
    penalty += sum(r.levels.get("Rotation") == "High" for r in risks) * 5
    penalty += sum(r.levels.get("Fixture") == "High" for r in risks) * 2
    return max(0, min(100, 100 - penalty))


def build_fixture_timeline(player: Player, fixtures: Sequence[Fixture], teams: Sequence[Any], limit: int = 5) -> List[Dict[str, Any]]:
    teams_by_id = {team.id: team for team in teams}
    timeline = []
    for fixture in fixtures:
        if player.team_id not in (fixture.team_h, fixture.team_a):
            continue
        home = fixture.team_h == player.team_id
        opponent_id = fixture.team_a if home else fixture.team_h
        opponent = teams_by_id.get(opponent_id)
        timeline.append({"gameweek": fixture.event, "opponent": opponent.short_name if opponent else (fixture.team_a_short_name if home else fixture.team_h_short_name) or "TBD", "home": home, "fdr": fixture.team_h_difficulty if home else fixture.team_a_difficulty, "kickoff": fixture.kickoff_time})
        if len(timeline) >= limit:
            break
    return timeline


def evaluate_transfer_plan(
    squad: Sequence[Player], moves: Sequence[Tuple[Player, Player]], bank: float, free_transfers: int,
    repository, scoring: DecisionScoringEngine, transfer_analyzer: Optional[TransferAnalyzer] = None,
) -> TransferPlan:
    issues: List[str] = []
    squad_by_id = {p.id: p for p in squad}
    outs = [pair[0] for pair in moves]
    ins = [pair[1] for pair in moves]
    if len({p.id for p in outs}) != len(outs) or len({p.id for p in ins}) != len(ins):
        issues.append("Each player may appear in the transfer list only once.")
    for player in outs:
        if player.id not in squad_by_id:
            issues.append(f"{player.web_name} is not in the saved squad.")
    for player in ins:
        if player.id in squad_by_id and player.id not in {p.id for p in outs}:
            issues.append(f"{player.web_name} is already in the saved squad.")
    final_players = [p for p in squad if p.id not in {x.id for x in outs}] + ins
    valid, composition_issues = validate_squad(final_players)
    # Squad changes are necessarily exactly 15, but retain distinct, explicit diagnostics.
    issues.extend(composition_issues)
    cost_delta = round(sum(p.cost_float for p in ins) - sum(p.cost_float for p in outs), 1)
    bank_after = round(bank - cost_delta, 1)
    affordable = bank_after >= 0
    if not affordable:
        issues.append(f"Transfer plan exceeds the available bank by £{abs(bank_after):.1f}m.")
    club_counts: Dict[int, int] = {}
    for player in final_players:
        club_counts[player.team_id] = club_counts.get(player.team_id, 0) + 1
    if any(count > 3 for count in club_counts.values()):
        issues.append("The resulting squad would exceed the 3-player club limit.")

    score_delta = 0.0
    fixture_delta = 0.0
    risk_delta = 0
    simulations = []
    for player_out, player_in in moves:
        fx_out = repository.get_upcoming_fixtures(team_id=player_out.team_id, limit=3)
        fx_in = repository.get_upcoming_fixtures(team_id=player_in.team_id, limit=3)
        out_score = scoring.evaluate_player(player_out, fx_out)
        in_score = scoring.evaluate_player(player_in, fx_in)
        score_delta += in_score.final_score - out_score.final_score
        fdr_delta = calculate_avg_fdr(fx_out, player_out.team_id) - calculate_avg_fdr(fx_in, player_in.team_id)
        fixture_delta += fdr_delta
        risk_delta += _basic_risk_points(player_out) - _basic_risk_points(player_in)
        if transfer_analyzer:
            evaluation = transfer_analyzer.evaluate(player_out, player_in)
            score = evaluation.confidence_delta
        else:
            score = round(in_score.final_score - out_score.final_score, 1)
        simulations.append({"out": player_out, "in": player_in, "score_out": out_score, "score_in": in_score, "decision_delta": score, "fixture_delta": round(fdr_delta, 2)})
    if len(moves) > free_transfers:
        issues.append(f"{len(moves) - free_transfers} transfer(s) exceed the {free_transfers} free transfer(s); point hits may apply.")
    legal = valid and not any("club limit" in issue for issue in issues)
    verdict = "Not affordable" if not affordable else "Illegal squad" if not legal else "Favorable move" if score_delta > 5 else "Sideways move" if score_delta >= -5 else "Lower-scoring move"
    return TransferPlan(list(moves), round(cost_delta, 1), bank_after, affordable, legal, issues, round(score_delta, 1), risk_delta, round(fixture_delta, 2), verdict, simulations)


def _basic_risk_points(player: Player) -> int:
    return 3 if player.status in {"i", "s", "u"} or player.chance_of_playing_next_round == 0 else 1 if player.status == "d" or (player.chance_of_playing_next_round is not None and player.chance_of_playing_next_round < 100) else 0


def find_alternatives(
    outgoing: Player,
    squad: Sequence[Player],
    repository,
    scoring: DecisionScoringEngine,
    available_budget: float,
    limit: int = 5,
) -> List[Tuple[Player, float]]:
    squad_ids = {p.id for p in squad}
    squad_counts: Dict[int, int] = {}
    for player in squad:
        squad_counts[player.team_id] = squad_counts.get(player.team_id, 0) + 1
    # A replacement frees the outgoing slot at the player's current club.
    squad_counts[outgoing.team_id] = max(0, squad_counts.get(outgoing.team_id, 0) - 1)
    candidates = repository.get_players(position=outgoing.position, max_price=outgoing.cost_float + available_budget, limit=700)
    scored = []
    for player in candidates:
        if player.id in squad_ids or player.id == outgoing.id:
            continue
        if squad_counts.get(player.team_id, 0) >= 3:
            continue
        fixtures = repository.get_upcoming_fixtures(team_id=player.team_id, limit=5)
        if not fixtures or player.status in {"i", "s", "u"}:
            continue
        score = scoring.evaluate_player(player, fixtures).final_score
        scored.append((player, score))
    return sorted(scored, key=lambda item: (item[1], item[0].form, item[0].total_points), reverse=True)[:limit]


@dataclass
class PlayerSearchFilters:
    position: Optional[str] = None
    max_price: Optional[float] = None
    price_exclusive: bool = False
    min_form: Optional[float] = None
    available_only: bool = False
    good_fixtures: bool = False
    min_last_five_average: Optional[float] = None
    horizon: int = 3


def parse_player_search(query: str) -> PlayerSearchFilters:
    lowered = query.casefold()
    position = next((p for p, words in {"MID": ("midfielder", "midfielders", "mid"), "FWD": ("forward", "forwards", "striker", "strikers"), "DEF": ("defender", "defenders"), "GKP": ("goalkeeper", "goalkeepers")}.items() if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)), None)
    price = re.search(r"(?:under|below|less than|max(?:imum)?|<)\s*£?\s*(\d+(?:\.\d+)?)", lowered)
    horizon = re.search(r"next\s+(\d+)\s+(?:gameweeks?|gws?)", lowered)
    return PlayerSearchFilters(
        position=position,
        max_price=float(price.group(1)) if price else None,
        price_exclusive=bool(price and re.search(r"\b(under|below|less than)\b|<", lowered)),
        min_form=5.0 if any(word in lowered for word in ("good form", "high form", "in form")) else None,
        available_only=any(word in lowered for word in ("available", "no major injury", "fit and available", "no injury concerns")),
        good_fixtures=any(word in lowered for word in ("good fixture", "easy fixture", "favorable fixture", "favourable fixture")),
        min_last_five_average=4.0 if "last 5" in lowered or "last five" in lowered else None,
        horizon=max(1, min(5, int(horizon.group(1)))) if horizon else 3,
    )


def search_players_natural_language(query: str, repository, scoring: DecisionScoringEngine, limit: int = 30) -> List[Dict[str, Any]]:
    filters = parse_player_search(query)
    players = repository.get_players(position=filters.position, max_price=filters.max_price, limit=700)
    results = []
    for player in players:
        if filters.available_only and (player.status != "a" or (player.chance_of_playing_next_round is not None and player.chance_of_playing_next_round < 75)):
            continue
        if filters.price_exclusive and filters.max_price is not None and player.cost_float >= filters.max_price:
            continue
        if filters.min_form is not None and player.form < filters.min_form:
            continue
        fixtures = repository.get_upcoming_fixtures(team_id=player.team_id, limit=filters.horizon)
        avg_fdr = calculate_avg_fdr(fixtures, player.team_id, filters.horizon)
        if filters.good_fixtures and avg_fdr > 3.0:
            continue
        history = repository.get_player_gameweek_history(player.id, limit=5)
        trend = calculate_form_trend(player, history)
        if filters.min_last_five_average is not None and (trend.average_points is None or trend.average_points < filters.min_last_five_average):
            continue
        score = scoring.evaluate_player(player, fixtures).final_score
        results.append({"player": player, "fixtures": fixtures, "avg_fdr": avg_fdr, "trend": trend, "score": score})
    return sorted(results, key=lambda item: (item["score"], item["player"].form, item["player"].total_points), reverse=True)[:limit]


def build_squad_report(
    players: Sequence[Player], repository, scoring: DecisionScoringEngine, captain_picker: CaptainPicker,
    vector_store=None, llm_provider=None, bank_millions: float = 0.0,
) -> SquadReport:
    evidence: List[Dict[str, Any]] = []
    evidence_by_player: Dict[int, List[Dict[str, Any]]] = {}
    priority = sorted(players, key=lambda p: (p.status not in {"i", "d", "s", "u"}, p.chance_of_playing_next_round or 0, p.form))[:8]
    if vector_store:
        for player in priority:
            query = f"{player.web_name} {player.team_name or ''} injury availability fitness return manager update minutes rotation"
            try:
                docs = vector_store.query(query, n_results=2)
            except Exception:
                docs = []
            evidence_by_player[player.id] = docs
            evidence.extend(docs)
    else:
        evidence_by_player = {player.id: [] for player in players}

    negative_ids = {pid for pid, docs in evidence_by_player.items() if any(str(d.get("metadata", {}).get("severity", "")).casefold() in {"out", "injured", "doubtful"} for d in docs)}
    xi = recommend_starting_xi(players, repository, scoring, negative_ids)
    risks = []
    trends = []
    injury_profiles: Dict[int, Dict[str, Any]] = {}
    for player in players:
        fixtures = repository.get_upcoming_fixtures(team_id=player.team_id, limit=5)
        history = repository.get_player_gameweek_history(player.id, limit=5)
        risks.append(analyze_player_risk(player, fixtures, history, evidence_by_player.get(player.id, [])))
        trends.append(calculate_form_trend(player, history))
        player_docs = [d for d in evidence_by_player.get(player.id, []) if _evidence_mentions_player(d, player)]
        if player.news or player_docs:
            candidates = [{"text": d.get("text", ""), "date": _doc_date(d), "source": d.get("metadata", {}).get("source", ""), "url": _doc_url(d), "metadata": d.get("metadata", {})} for d in player_docs]
            if player.news:
                candidates.append({"text": player.news, "date": player.news_added, "source": "Premier League", "url": "", "metadata": {"source": "Premier League / FPL Medical Bulletin", "published_at": player.news_added or "", "player": player.web_name}})
            reconciliation = reconcile_injury_sources(candidates, repository.get_upcoming_fixtures(team_id=player.team_id, limit=38), player.team_id)
            injury_profiles[player.id] = {"reconciliation": reconciliation, "availability": player.status, "photo": player.photo_url}
    captains = captain_picker.get_top_captains(top_n=2, negative_news_player_ids=negative_ids, candidate_players=list(players))
    health = squad_health_score(players, risks)
    injury_count = sum(p.status in {"i", "s", "u"} or p.chance_of_playing_next_round == 0 for p in players)
    doubtful = sum(
        p.status not in {"i", "s", "u"}
        and p.chance_of_playing_next_round != 0
        and (p.status == "d" or (p.chance_of_playing_next_round is not None and p.chance_of_playing_next_round < 100))
        for p in players
    )
    rotation = sum(r.levels["Rotation"] in {"Medium", "High"} for r in risks)
    difficult = sum(r.levels["Fixture"] == "High" for r in risks)

    outgoing = sorted(players, key=lambda p: (xi.scores.get(p.id, 0), p.form, p.total_points))[:3]
    alternatives = {}
    for player in outgoing:
        alternatives[player.id] = find_alternatives(player, players, repository, scoring, available_budget=bank_millions, limit=3)

    report = SquadReport(xi, captains, risks, health, injury_count, doubtful, rotation, difficult, outgoing, alternatives, trends, evidence, injury_profiles)
    if llm_provider:
        context = _report_context(report)
        system = "Explain this FPL squad report using only supplied data and source evidence. Do not add numbers, scores, fixtures, medical details, dates, sources or ranks. Explain Python-computed decisions; do not change them."
        try:
            answer = llm_provider.generate_response(context, system_instruction=system)
            report.explanation = answer if _grounded_numbers(answer, context) else "The deterministic squad report is ready. The explanation was omitted because it included unsupported numeric information."
        except Exception:
            report.explanation = "Offline mode: the report below is produced by the deterministic squad and fixture engines; current news evidence is shown with sources."
    return report


def _report_context(report: SquadReport) -> str:
    lines = ["SQUAD REPORT", f"Squad health: {report.health_score}/100", f"Formation: {report.xi.formation}"]
    lines.extend(f"XI: {p.web_name}, score {report.xi.scores[p.id]}/100, FPL status {p.status}" for p in report.xi.starting)
    lines.extend(f"Bench: {p.web_name}, score {report.xi.scores[p.id]}/100, FPL status {p.status}" for p in report.xi.bench)
    lines.extend(f"Captain candidate {i}: {c.player.web_name}, {c.captain_index}/100, vs {c.next_opponent}, FDR {c.fdr}" for i, c in enumerate(report.captains, 1))
    lines.extend(f"Risk: {risk.player.web_name} {category} {level}: {risk.reasons[category]}" for risk in report.risks for category, level in risk.levels.items())
    if report.evidence:
        lines.extend(f"Evidence: {d.get('metadata', {}).get('source', 'Unknown source')} {d.get('metadata', {}).get('published_at', 'Unknown date')} {d.get('text', '')[:700]}" for d in report.evidence[:10])
    else:
        lines.append("No relevant recent squad news evidence was found.")
    return "\n".join(lines)


def _doc_date(doc):
    metadata = doc.get("metadata", {})
    return metadata.get("published_at") or metadata.get("updated_at") or metadata.get("timestamp")


def _doc_url(doc):
    metadata = doc.get("metadata", {})
    url = metadata.get("url") or metadata.get("link") or metadata.get("source_url")
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else ""


def _evidence_mentions_player(doc, player):
    metadata = doc.get("metadata", {})
    tagged = " ".join(str(metadata.get(key, "")) for key in ("player", "player_tags", "title")).casefold()
    return player.web_name.casefold() in tagged or player.full_name.casefold() in tagged


def _grounded_numbers(answer, context):
    import re
    values = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", context))
    claims = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", answer))
    return claims.issubset(values)
