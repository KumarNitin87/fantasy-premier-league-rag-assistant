"""Deterministic intent and entity parsing for common FPL questions."""

from dataclasses import dataclass, field
import re
from typing import List, Optional

from src.database.models import Player


@dataclass
class FPLQuery:
    raw: str
    intent: str = "general_fpl"
    players: List[Player] = field(default_factory=list)
    teams: List[object] = field(default_factory=list)
    player_out: Optional[Player] = None
    player_in: Optional[Player] = None
    gameweek: Optional[int] = None
    horizon: int = 3
    position: Optional[str] = None
    ambiguous: List[str] = field(default_factory=list)


def understand_query(query: str, repository) -> FPLQuery:
    text = query.strip()
    lowered = text.casefold()
    parsed = FPLQuery(raw=text)
    gw = re.search(r"\b(?:gw|gameweek|week)\s*(\d{1,2})\b", lowered)
    parsed.gameweek = int(gw.group(1)) if gw else None
    horizon = re.search(r"\b(?:next|over|for the next)\s+(\d+)\s+(?:gameweeks?|gws?|weeks?)\b", lowered)
    parsed.horizon = max(1, min(5, int(horizon.group(1)))) if horizon else 3
    for label, pattern in (("GKP", r"\bgoalkeepers?\b"), ("DEF", r"\bdefenders?\b"), ("MID", r"\bmidfielders?\b"), ("FWD", r"\bforwards?|strikers?\b")):
        if re.search(pattern, lowered):
            parsed.position = label
            break

    # Resolve player mentions by exact FPL display/full name first, then safe unique partial.
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", re.sub(r"(?<=\w)'s\b", "", text, flags=re.IGNORECASE))
    ignored = {"should", "transfer", "out", "for", "bring", "into", "team", "captain", "this", "week", "gameweek", "latest", "injury", "situation", "status", "is", "likely", "start", "who", "better", "option", "next", "the", "and", "or", "what", "about", "make", "based", "current", "news", "fixtures", "player", "compare", "compare", "with", "from", "to", "have", "good", "fixtures", "favorable", "favourable", "no", "major", "concerns"}
    candidates = []
    # Pairs before singles allows full names; exact match avoids substring ambiguity.
    probes = [" ".join(words[i:i+2]) for i in range(len(words)-1)] + words
    seen = set()
    for probe in probes:
        if probe.casefold() in ignored or len(probe) < 3 or probe.casefold() in seen:
            continue
        seen.add(probe.casefold())
        matches = repository.search_players(probe, limit=12)
        if not matches:
            continue
        exact = [p for p in matches if probe.casefold() in {p.web_name.casefold(), p.full_name.casefold(), f"{p.first_name} {p.second_name}".casefold()}]
        pool = exact or matches
        # Prefer unique exact web/full-name match; never guess among tied partial matches.
        picked = pool[0] if len(pool) == 1 else None
        if picked is None and len(exact) == 1:
            picked = exact[0]
        if picked:
            if all(existing.id != picked.id for existing in candidates):
                candidates.append(picked)
        elif len(pool) > 1 and probe.casefold() not in ignored:
            parsed.ambiguous.append(probe)
    parsed.players = candidates[:4]
    if hasattr(repository, "get_all_teams"):
        all_teams = repository.get_all_teams()
        for team in all_teams:
            aliases = [getattr(team, "name", ""), getattr(team, "short_name", "")]
            if any(alias and re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", text, re.I) for alias in aliases):
                parsed.teams.append(team)

    if re.search(r"\b(captain|captaincy|armband)\b", lowered):
        parsed.intent = "captain_analysis"
    elif re.search(r"\b(injury|injured|fitness|availability|return date|returning)\b", lowered):
        parsed.intent = "injury_analysis"
    elif re.search(r"\b(transfer|bring .* into|sell|buy|swap|out for)\b", lowered):
        parsed.intent = "transfer_analysis"
    elif len(parsed.players) > 1 and re.search(r"\b(compare|better|versus|\bvs\b|option)\b", lowered):
        parsed.intent = "comparison_analysis"
    elif re.search(r"\b(fixture|fixtures|schedule|gameweek|fdr|opponent)\b", lowered):
        parsed.intent = "fixture_analysis"
    elif len(parsed.players) > 1:
        parsed.intent = "comparison_analysis"

    if parsed.intent == "transfer_analysis" and len(parsed.players) >= 2:
        parsed.player_out, parsed.player_in = parsed.players[0], parsed.players[1]
    return parsed
