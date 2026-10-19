"""Grounded Phase 3 orchestration: FPL data + RAG + deterministic scores + explanation."""

from dataclasses import dataclass, field
from datetime import date, datetime
import logging
import re
from typing import Any, Dict, List, Optional

from src.analytics.metrics import calculate_avg_fdr
from src.rag.injury_intelligence import reconcile_injury_sources
from src.rag.query_understanding import FPLQuery, understand_query

logger = logging.getLogger(__name__)


@dataclass
class AssistantResult:
    parsed: FPLQuery
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    player_contexts: List[Any] = field(default_factory=list)
    scores: Dict[int, Any] = field(default_factory=dict)
    transfer: Any = None
    captains: List[Any] = field(default_factory=list)
    team_fixtures: Dict[str, List[Any]] = field(default_factory=dict)
    gameweek_fixtures: List[Any] = field(default_factory=list)
    current_gameweek: Optional[int] = None
    injury: Optional[Dict[str, Any]] = None
    answer: str = ""
    provider_name: str = ""
    fallback: bool = False
    grounded_context: str = ""


class FPLAssistant:
    def __init__(self, retriever, llm_provider, scoring_engine, transfer_analyzer=None, captain_picker=None):
        self.retriever = retriever
        self.repo = retriever.repo
        self.llm = llm_provider
        self.scoring = scoring_engine
        self.transfer_analyzer = transfer_analyzer
        self.captain_picker = captain_picker

    def analyze(self, question: str, prior_transfer_out: Optional[str] = None) -> AssistantResult:
        parsed = understand_query(question, self.repo)
        if prior_transfer_out and len(parsed.players) == 1 and re.search(r"\b(what about|instead|how about)\b", question, re.I):
            old = self.repo.search_players(prior_transfer_out, limit=5)
            if old:
                parsed.intent = "transfer_analysis"
                parsed.player_out, parsed.player_in = old[0], parsed.players[0]
                parsed.players = [parsed.player_out, parsed.player_in]
        if parsed.intent == "transfer_analysis" and parsed.player_out is None and prior_transfer_out:
            matches = self.repo.search_players(prior_transfer_out, limit=5)
            if matches:
                parsed.player_out = matches[0]
                parsed.players = [parsed.player_out] + parsed.players
                if parsed.players and parsed.players[0].id != parsed.player_out.id:
                    parsed.player_in = parsed.players[1]
        logger.info("FPL assistant intent=%s players=%s", parsed.intent, [p.web_name for p in parsed.players])
        result = AssistantResult(parsed=parsed, provider_name=getattr(self.llm, "name", "unknown"))
        if hasattr(self.repo, "get_current_gameweek"):
            result.current_gameweek = self.repo.get_current_gameweek()
            if parsed.gameweek is None and re.search(r"\b(this week|this gameweek|current gameweek|upcoming gameweek)\b", question, re.I):
                parsed.gameweek = result.current_gameweek
        if parsed.intent == "fixture_analysis" and parsed.gameweek and hasattr(self.repo, "get_fixtures_by_gameweek"):
            result.gameweek_fixtures = self.repo.get_fixtures_by_gameweek(parsed.gameweek)
        for team in parsed.teams:
            result.team_fixtures[team.name] = self.repo.get_upcoming_fixtures(team_id=team.id, limit=parsed.horizon)
            team_ctx = self.retriever.retrieve(f"{team.name} {question}", top_k_news=5)
            result.player_contexts.append(team_ctx)
            result.evidence.extend(team_ctx.news_chunks)

        if parsed.intent == "captain_analysis" and self.captain_picker:
            result.captains = self.captain_picker.get_top_captains(top_n=5)
            for suggestion in result.captains:
                result.scores[suggestion.player.id] = suggestion.score_breakdown

        query_players = parsed.players
        if parsed.intent == "captain_analysis":
            query_players = list(dict((p.id, p) for p in [item.player for item in result.captains] + query_players).values())
        for player in query_players:
            ctx = self.retriever.retrieve(f"{player.web_name} {player.team_name or ''} {question}", top_k_news=5, fixture_limit=parsed.horizon)
            result.player_contexts.append(ctx)
            result.evidence.extend(ctx.news_chunks)
            adverse = any(str(c.get("metadata", {}).get("severity", "")).casefold() in {"out", "injured", "doubtful"} for c in ctx.news_chunks)
            result.scores[player.id] = self.scoring.evaluate_player(player, ctx.upcoming_fixtures, has_negative_news=adverse)
            logger.info("FPL assistant database retrieval player=%s fixtures=%d history=%d", player.web_name, len(ctx.upcoming_fixtures), len(ctx.player_history))
            logger.info("FPL assistant RAG retrieval player=%s documents=%d score=%.1f", player.web_name, len(ctx.news_chunks), result.scores[player.id].final_score)

        if parsed.intent == "captain_analysis" and self.captain_picker:
            negative_ids = {ctx.target_player.id for ctx in result.player_contexts if ctx.target_player and _has_negative_evidence(ctx.news_chunks)}
            result.captains = self.captain_picker.get_top_captains(top_n=5, negative_news_player_ids=negative_ids)
            for suggestion in result.captains:
                result.scores[suggestion.player.id] = suggestion.score_breakdown

        # Deduplicate and freshness-rank retrieved records, preserving source metadata.
        result.evidence = _rank_evidence(_deduplicate(result.evidence))
        if parsed.intent == "transfer_analysis" and parsed.player_out and parsed.player_in and self.transfer_analyzer:
            result.transfer = self.transfer_analyzer.evaluate(
                parsed.player_out,
                parsed.player_in,
                has_negative_news_out=_has_negative_evidence(_evidence_for(result, parsed.player_out)),
                has_negative_news_in=_has_negative_evidence(_evidence_for(result, parsed.player_in)),
            )
            logger.info("FPL assistant transfer score_delta=%s", result.transfer.confidence_delta)

        if parsed.intent == "injury_analysis" and parsed.players:
            player = parsed.players[0]
            fixtures = self.repo.get_upcoming_fixtures(team_id=player.team_id, limit=38)
            linked = [c for c in result.evidence if _linked_to_player(c, player)]
            candidates = [
                {"text": c.get("text", ""), "date": _source_date(c), "source": c.get("metadata", {}).get("source", ""), "url": _source_url(c), "metadata": c.get("metadata", {})}
                for c in linked
            ]
            if player.news:
                fpl_evidence = {
                    "id": f"fpl-status-{player.id}", "text": player.news, "similarity": None,
                    "metadata": {"source": "Premier League / FPL Medical Bulletin", "published_at": player.news_added or "", "category": "injury", "player": player.web_name},
                }
                candidates.append({"text": player.news, "date": player.news_added, "source": "Premier League", "url": "", "metadata": fpl_evidence["metadata"]})
                result.evidence.append(fpl_evidence)
                result.evidence = _rank_evidence(_deduplicate(result.evidence))
            reconciled = reconcile_injury_sources(candidates, fixtures, player.team_id)
            result.injury = {
                "player": player,
                "availability": _availability(player),
                "reconciliation": reconciled if linked else None,
                "evidence": linked,
            }

        result.grounded_context = self._context(question, parsed, result)
        logger.info("FPL assistant rag_documents=%d sources=%s", len(result.evidence), sorted({c.get("metadata", {}).get("source", "") for c in result.evidence}))
        system = (
            "You are an FPL explanation layer. Use only the supplied grounded context. Do not invent or alter player names, "
            "numbers, scores, fixtures, dates, injuries, source names or URLs. Do not calculate or override decision scores. "
            "Say when information is unavailable. Attribute every news/injury claim to supplied evidence. Keep the answer concise."
        )
        try:
            result.answer = self.llm.generate_response(result.grounded_context, system_instruction=system)
            if not _response_is_grounded(result.answer, result.grounded_context, parsed):
                logger.warning("FPL assistant rejected explanation with unsupported factual tokens")
                result.answer = _offline_summary(parsed, result)
                result.fallback = True
        except Exception:
            logger.exception("FPL assistant provider failed; using grounded offline summary")
            result.answer = _offline_summary(parsed, result)
            result.fallback = True
        # Gemini provider itself can transparently return its existing offline fallback.
        result.fallback = result.fallback or "Offline Mock" in result.provider_name or "offline analysis" in result.answer.casefold()
        if not result.evidence and "no relevant recent news or injury evidence was found" not in result.answer.casefold():
            result.answer = result.answer.rstrip() + "\n\nNo relevant recent news or injury evidence was found."
        logger.info("FPL assistant provider=%s fallback=%s", result.provider_name, result.fallback)
        return result

    def _context(self, question: str, parsed: FPLQuery, result: AssistantResult) -> str:
        lines = [f"USER QUESTION: {question}", f"INTENT: {parsed.intent}", "RULE: Numeric scores below are deterministic Python outputs; explain, do not change."]
        if result.current_gameweek is not None:
            lines.append(f"Current upcoming FPL Gameweek (derived from unplayed fixtures): GW{result.current_gameweek}")
        if parsed.ambiguous:
            lines.append("AMBIGUOUS PLAYER MATCH: ask user to clarify: " + ", ".join(parsed.ambiguous))
        for ctx in result.player_contexts:
            p = ctx.target_player
            if not p:
                continue
            lines.extend(["\nPLAYER DATA", f"Name: {p.full_name} ({p.web_name})", f"Club: {p.team_name}", f"Position: {p.position}", f"Price: £{p.cost_float:.1f}m", f"Form: {p.form}", f"Total points: {p.total_points}", f"Points per game: {p.points_per_game}", f"Minutes: {p.minutes}", f"Goals: {p.goals_scored}; assists: {p.assists}; clean sheets: {p.clean_sheets}", f"xG: {p.expected_goals}; xA: {p.expected_assists}; ICT: {p.ict_index}; ownership: {p.selected_by_percent}%", f"Availability: {_availability(p)}", f"Official FPL bulletin: {p.news or 'Not available'}"])
            lines.append("FIXTURES")
            for fx in ctx.upcoming_fixtures[:parsed.horizon]:
                home = fx.team_h == p.team_id
                opponent = fx.team_a_name if home else fx.team_h_name
                fdr = fx.team_h_difficulty if home else fx.team_a_difficulty
                lines.append(f"GW{fx.event}: {opponent or 'Unknown'} ({'Home' if home else 'Away'}), FDR {fdr}, kickoff {fx.kickoff_time or 'Not available'}")
            score = result.scores.get(p.id)
            if score:
                lines.extend(["DETERMINISTIC SCORE", f"Decision Confidence Score: {score.final_score}/100", f"Tier: {score.tier}", f"Positive factors: {', '.join(score.positive_reasons) or 'None recorded'}", f"Risks: {', '.join(score.negative_reasons) or 'None recorded'}"])
            history = ctx.player_history[:3]
            if history:
                lines.append("RECENT HISTORY: " + "; ".join(f"GW{h.round} {h.total_points} pts/{h.minutes} min" for h in history))
        if result.transfer:
            t = result.transfer
            lines.extend(["TRANSFER ANALYSIS", f"{t.player_out.web_name} -> {t.player_in.web_name}", f"Cost change: £{t.cost_diff:+.1f}m; affordability: {'affordable at £0 bank assumption' if t.is_affordable else 'not affordable at £0 bank assumption'}", f"OUT score: {t.score_out.final_score}/100; IN score: {t.score_in.final_score}/100; score delta: {t.confidence_delta:+.1f}", f"Verdict: {t.verdict}"])
        if result.captains:
            lines.append("DETERMINISTIC CAPTAIN RANKING")
            lines.extend(f"{s.rank}. {s.player.web_name}: {s.captain_index}/100, vs {s.next_opponent} ({'H' if s.is_home else 'A'}, FDR {s.fdr}), form {s.player.form}, status {_availability(s.player)}" for s in result.captains)
        for team_name, fixtures in result.team_fixtures.items():
            lines.append(f"TEAM FIXTURES — {team_name}")
            for fx in fixtures:
                home = fx.team_h_name == team_name or (fx.team_h_short_name and any(fx.team_h_short_name == t.short_name for t in parsed.teams if t.name == team_name))
                opponent = fx.team_a_name if home else fx.team_h_name
                fdr = fx.team_h_difficulty if home else fx.team_a_difficulty
                lines.append(f"GW{fx.event}: {opponent or 'Unknown'} ({'Home' if home else 'Away'}), FDR {fdr}, kickoff {fx.kickoff_time or 'Not available'}")
        if result.gameweek_fixtures:
            lines.append(f"GAMEWEEK {parsed.gameweek} FIXTURES")
            for fx in result.gameweek_fixtures[:20]:
                lines.append(f"{fx.team_h_name or 'Home team'} vs {fx.team_a_name or 'Away team'} · FDR {fx.team_h_difficulty}/{fx.team_a_difficulty} · {fx.kickoff_time or 'Date unavailable'}")
        if result.injury:
            i = result.injury
            lines.extend(["INJURY PROFILE", f"Current FPL availability: {i['availability']}"])
            rec = i.get("reconciliation")
            info = rec.get("info", {}) if rec else {}
            lines.append(f"Injury start: {info.get('injury_start_date') or 'Not available'}")
            lines.append(f"Expected return: {info.get('expected_return_date') or 'Not publicly available'} ({info.get('return_date_confidence', 'Unknown')})")
            lines.append(f"Estimated recovery: {info.get('recovery_duration') or 'Not available'}; fixture-derived matchweek: {info.get('matchweek') or 'Not available'}")
            if rec and rec.get("conflict"):
                lines.append("RETURN DATE CONFLICT: Sources disagree. Both source records are listed below; selected display prioritizes source authority and freshness.")
            if rec and rec.get("stale"):
                lines.append("RETURN EVIDENCE WARNING: Selected source is stale; treat its return information cautiously.")
        lines.append("\nNEWS / INJURY EVIDENCE")
        if result.evidence:
            for idx, c in enumerate(result.evidence[:8], 1):
                m = c.get("metadata", {})
                lines.append(f"[{idx}] Source: {m.get('source') or 'Unknown source'}; published: {_source_date(c) or 'Unknown date'}; type: {m.get('category') or 'news'}; URL: {_source_url(c) or 'Not available'}; player/team tags: {m.get('player_tags') or m.get('team_tags') or 'Not available'}; evidence: {c.get('text', '')[:900]}")
        else:
            lines.append("No relevant recent news or injury evidence was found.")
        return "\n".join(lines)


def _availability(player):
    labels = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable"}
    chance = player.chance_of_playing_next_round
    return f"{labels.get(player.status, player.status)}" + (f" ({chance}% chance)" if chance is not None else "")


def _has_negative_evidence(chunks):
    return any(str(c.get("metadata", {}).get("severity", "")).casefold() in {"out", "injured", "doubtful"} for c in chunks)


def _evidence_for(result, player):
    return next((ctx.news_chunks for ctx in result.player_contexts if ctx.target_player and ctx.target_player.id == player.id), [])


def _source_date(item):
    meta = item.get("metadata", {})
    return meta.get("published_at") or meta.get("updated_at") or meta.get("timestamp") or ""


def _source_url(item):
    meta = item.get("metadata", {})
    value = meta.get("url") or meta.get("link") or meta.get("source_url")
    return value if isinstance(value, str) and value.startswith(("https://", "http://")) else ""


def _linked_to_player(item, player):
    m = item.get("metadata", {})
    names = " ".join([str(m.get(k, "")) for k in ("player", "player_tags", "title")]).casefold()
    return player.web_name.casefold() in names or player.full_name.casefold() in names


def _deduplicate(items):
    unique = {}
    for item in items:
        key = item.get("id") or (item.get("text"), _source_url(item))
        unique[key] = item
    return list(unique.values())


def _rank_evidence(items):
    today = date.today()
    def rank(item):
        meta = item.get("metadata", {})
        raw_date = _source_date(item)
        try:
            published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
            age = max(0, (today - published).days)
        except (ValueError, TypeError):
            age = 9999
        player = bool(meta.get("player") or meta.get("player_tags"))
        injury = str(meta.get("category", "")).casefold() == "injury"
        return (bool(raw_date), player, injury, -min(age, 365), float(item.get("similarity") or 0))
    return sorted(items, key=rank, reverse=True)


def _response_is_grounded(answer, context, parsed):
    # Guard against introducing numeric facts that do not occur in the supplied data.
    context_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", context))
    answer_numbers = set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", answer))
    if not answer_numbers.issubset(context_numbers):
        return False
    month = r"January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    date_patterns = (rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{month})\s+\d{{4}}\b", rf"\b(?:{month})\s+\d{{1,2}},?\s+\d{{4}}\b", r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}/\d{1,2}/\d{4}\b")
    context_dates = {m.group(0).casefold() for pattern in date_patterns for m in re.finditer(pattern, context, re.I)}
    answer_dates = {m.group(0).casefold() for pattern in date_patterns for m in re.finditer(pattern, answer, re.I)}
    if not answer_dates.issubset(context_dates):
        return False
    known_names = {p.web_name.casefold() for p in parsed.players}
    supported_entities = set(known_names)
    for line in context.splitlines():
        if line.startswith(("Name:", "Club:")):
            value = line.split(":", 1)[1]
            supported_entities.update(token.casefold() for token in re.findall(r"[A-Z][A-Za-z'-]+", value))
    for value in re.findall(r"Source:\s*([^;\n]+)", context):
        supported_entities.update(token.casefold() for token in re.findall(r"[A-Z][A-Za-z'-]+", value))
    supported_entities.update(token.casefold() for token in re.findall(r"\b[A-Z][a-zA-Z'-]{2,}\b", context.split("NEWS / INJURY EVIDENCE", 1)[-1]))
    mentioned = {word.casefold() for word in re.findall(r"\b[A-Z][a-zA-Z'-]{2,}\b", answer)}
    # Sentence-initial words are common grammar and are not treated as player names.
    allowed = supported_entities | {"fpl", "gw", "fdr", "ai", "i", "the", "this", "based", "current", "offline", "mock", "analysis", "form", "availability", "fixtures", "injury", "evidence", "score", "confidence", "decision", "no", "not", "unknown", "available", "reported", "estimated", "confirmed", "return", "source", "risk", "club", "price", "points", "minutes", "goals", "assists", "expected", "matchweek", "gameweek", "home", "away", "favorable", "favourable", "fixture", "status", "statistical", "recommendation", "consider", "however", "because", "while", "provided", "retrieved", "relevant", "recent", "grounded", "sourced", "there", "it", "he", "she", "they", "his", "her", "their", "can", "could", "would", "should", "will", "may", "trade", "off", "also", "from", "with", "for", "and", "but", "or", "after", "before", "strong", "better", "higher", "lower", "less", "more", "than", "as", "at", "to", "in", "of", "on", "by", "is", "are", "was", "were", "has", "have", "had", "be", "been", "being", "shows", "indicates", "suggests", "supports", "using", "data", "please", "clarify", "player", "name", "match", "found", "ambiguous", "question", "enough", "structured", "give", "a", "an", "action", "acting", "see", "below", "latest", "next", "upcoming", "publicly", "only", "all", "both", "compared", "versus", "per", "million", "pts", "against", "faces", "facing", "option", "pick", "captain", "captains", "ranked", "engine", "candidate", "candidates", "point", "delta", "vs", "out", "cost", "change", "transfer", "neutral", "move", "verdict", "news", "started", "recovery", "situation", "fitness", "chance", "playing", "direct", "answer", "index", "supporting", "primary", "secondary", "updated", "published", "date", "unavailable", "statistics", "schedule", "difficulty", "position", "ownership", "ict", "xg", "xa", "team", "official", "premier", "league", "football", "no", "or", "was", "ask", "provide", "more", "specific", "details", "resolve", "retrieve", "for"}
    return mentioned.issubset(allowed)


def _offline_summary(parsed, result):
    if parsed.ambiguous:
        return "I found an ambiguous player match. Please clarify the player name before I analyze it."
    if not result.player_contexts and not result.captains:
        return "I could not resolve a player or retrieve enough structured FPL data for this question. No relevant recent news or injury evidence was found." if not result.evidence else "I retrieved evidence, but could not resolve enough structured FPL data to give a grounded recommendation."
    if result.transfer:
        t = result.transfer
        return f"Offline/mock analysis: {t.verdict}. The Python transfer engine scores {t.player_out.web_name} at {t.score_out.final_score}/100 and {t.player_in.web_name} at {t.score_in.final_score}/100 (delta {t.confidence_delta:+.1f}). Cost change is £{t.cost_diff:+.1f}m. Consider the displayed fixtures, availability, and sourced evidence before acting."
    if result.captains:
        return "Offline/mock analysis: captain candidates are ranked by the existing Python captain engine. " + "; ".join(f"{s.rank}. {s.player.web_name} ({s.captain_index}/100, vs {s.next_opponent}, FDR {s.fdr})" for s in result.captains[:5])
    if result.injury:
        rec = result.injury.get("reconciliation")
        info = rec.get("info", {}) if rec else {}
        return f"Offline/mock analysis: current FPL availability is {result.injury['availability']}. Injury start: {info.get('injury_start_date') or 'Not available'}. Expected return: {info.get('expected_return_date') or 'Not publicly available'}. Return confidence: {info.get('return_date_confidence', 'Unknown')}. " + ("No relevant recent news or injury evidence was found." if not result.injury.get("evidence") else "See the cited source evidence below.")
    return "Offline/mock analysis: " + "; ".join(f"{ctx.target_player.web_name}: {result.scores[ctx.target_player.id].final_score}/100 deterministic confidence; form {ctx.target_player.form}; availability {_availability(ctx.target_player)}." for ctx in result.player_contexts)
