"""Source-backed, conservative injury return extraction and reconciliation."""

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
DATE_TOKEN = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})(?:\s+\d{{4}})?|(?:{MONTHS})\s+\d{{1,2}}(?:,?\s+\d{{4}})?)"
NUM = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
RETURN_RE = re.compile(
    rf"\b(?:expected\s+back|expected\s+to\s+return|will\s+return|return(?:ing)?\s+(?:on|by)|back\s+(?:on|by)|available\s+(?:on|from|by))\s*[:,-]?\s*(?:(?:on|by)\s+)?(?P<date>{DATE_TOKEN})",
    re.IGNORECASE,
)
START_RE = re.compile(
    rf"\b(?:injured|injury|sustained\s+(?:an?\s+)?injury|suffered\s+(?:an?\s+)?injury)\s+(?:on|since|from)\s+(?P<date>{DATE_TOKEN})",
    re.IGNORECASE,
)
START_AFTER_DURATION_RE = re.compile(rf"\b(?:out|sidelined)\s+for\s+{NUM}\s+(?:days?|weeks?|months?)\s+from\s+(?P<date>{DATE_TOKEN})", re.IGNORECASE)
DURATION_RE = re.compile(
    rf"\b(?:out|sidelined|ruled\s+out)\s+for\s+(?P<out>{NUM})(?:\s*[-–]\s*(?P<out_high>{NUM}))?\s*(?P<out_unit>days?|weeks?|months?|matches|games)\b"
    rf"|\b(?:for|of|about|around|approximately|approx\.?|~)\s*(?P<low>{NUM})(?:\s*[-–]\s*(?P<high>{NUM}))?\s*(?P<unit>days?|weeks?|months?)\b"
    rf"|\b(?P<simple>{NUM})\s*(?P<simple_unit>days?|weeks?)\s+(?:out|sidelined)\b",
    re.IGNORECASE,
)
MONTH_RETURN_RE = re.compile(r"\b(?:expected\s+back|could\s+return|expected\s+to\s+return|back)\s+(?:in|next\s+month\s+in)\s+(?P<month>" + MONTHS + r")\b", re.IGNORECASE)
NEXT_MONTH_RE = re.compile(r"\b(?:could\s+return|expected\s+back|return\s+)?next\s+month\b", re.IGNORECASE)
MATCH_RETURN_RE = re.compile(rf"\b(?:expected\s+to\s+return\s+for|back\s+for|available\s+for)\s+(?:matchweek|gameweek|mw|gw)\s*(?P<gw>\d{{1,2}})\b", re.IGNORECASE)
NEXT_MATCHES_RE = re.compile(rf"\b(?:ruled\s+out\s+for\s+(?:(?:the\s+)?next\s+)?|miss(?:es|ing)\s+the\s+next\s+)(?P<count>{NUM})\s+(?:matches|games)\b", re.IGNORECASE)
WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}


def _number(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    return WORD_NUMBERS.get(raw.casefold(), int(raw) if raw.isdigit() else None)


def _parse_source_date(value: Any) -> Optional[date]:
    if not value:
        return None
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw[:25], fmt).date()
        except ValueError:
            continue
    return None


def _parse_date_token(raw: str, anchor: Optional[date]) -> Optional[date]:
    raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s+", " ", raw.replace(",", " ")).strip()
    formats = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d %B", "%d %b", "%B %d", "%b %d")
    for fmt in formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            if "%Y" in fmt:
                return parsed.date()
            if anchor is None:
                return None
            candidate = parsed.date().replace(year=anchor.year)
            if (candidate - anchor).days < -180:
                candidate = candidate.replace(year=anchor.year + 1)
            return candidate
        except ValueError:
            continue
    return None


def injury_source_priority(source_name: str) -> int:
    """Rank sources using the requested trusted-source hierarchy."""
    source = (source_name or "").casefold()
    if "official" in source and "premier league" not in source:
        return 0
    if "premier league" in source or source.startswith("fpl"):
        return 1
    if "transfermarkt" in source:
        return 2
    if any(name in source for name in ("bbc", "sky sports", "guardian", "associated press", "reuters")):
        return 3
    return 4


def _fixture_return_matchweek(fixtures: Iterable[Any], team_id: int, return_date: date) -> Optional[int]:
    candidates = []
    for fixture in fixtures:
        if getattr(fixture, "finished", False) or getattr(fixture, "event", None) is None:
            continue
        if team_id not in (getattr(fixture, "team_h", None), getattr(fixture, "team_a", None)):
            continue
        kickoff = _parse_source_date(getattr(fixture, "kickoff_time", None))
        if kickoff and kickoff >= return_date:
            candidates.append((kickoff, int(fixture.event)))
    return min(candidates)[1] if candidates else None


def _fixture_after_exclusions(fixtures: Iterable[Any], team_id: int, source_date: Optional[date], count: int) -> Optional[date]:
    upcoming = []
    for fixture in fixtures:
        if getattr(fixture, "finished", False) or team_id not in (getattr(fixture, "team_h", None), getattr(fixture, "team_a", None)):
            continue
        kickoff = _parse_source_date(getattr(fixture, "kickoff_time", None))
        if kickoff and (source_date is None or kickoff >= source_date):
            upcoming.append(kickoff)
    upcoming = sorted(upcoming)
    return upcoming[count] if count < len(upcoming) else None


def extract_injury_return_info(
    source_text: str,
    source_date: Any,
    fixtures: Iterable[Any],
    team_id: int,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Extract only explicit source evidence; relative dates need a known source date."""
    text = re.sub(r"\s+", " ", str(source_text or "")).strip()
    published = _parse_source_date(source_date)
    start_match = START_RE.search(text)
    if not start_match:
        start_match = START_AFTER_DURATION_RE.search(text)
    injury_start = _parse_date_token(start_match.group("date"), published) if start_match else None

    return_match = RETURN_RE.search(text)
    expected_return = _parse_date_token(return_match.group("date"), published) if return_match else None
    date_confidence = "Reported / Estimated" if expected_return else "Unknown"
    if expected_return and re.search(r"\b(?:confirmed\s+(?:available|to return|return date)|will\s+(?:be available|return)|available again)\b", text, re.I) and not re.search(r"\bnot\s+(?:yet\s+)?confirmed\b", text, re.I):
        date_confidence = "Confirmed"

    duration_match = DURATION_RE.search(text)
    recovery_days = recovery_weeks = None
    duration_text = None
    if duration_match:
        low = _number(duration_match.group("out") or duration_match.group("low") or duration_match.group("simple"))
        high = _number(duration_match.group("out_high") or duration_match.group("high")) or low
        unit = (duration_match.group("out_unit") or duration_match.group("unit") or duration_match.group("simple_unit") or "").lower()
        if low and unit.startswith(("day", "week", "month")):
            multiplier = 7 if unit.startswith("week") else 30 if unit.startswith("month") else 1
            recovery_days = (low * multiplier, high * multiplier)
            recovery_weeks = (round(recovery_days[0] / 7, 1), round(recovery_days[1] / 7, 1))
            duration_text = f"~{recovery_days[0]} days" if low == high else f"~{recovery_days[0]}–{recovery_days[1]} days"
            if published:
                # Explicit intervals are relative to injury date when sourced, otherwise publication date.
                base = injury_start or published
                expected_return = base + timedelta(days=recovery_days[0])
                date_confidence = "Reported / Estimated"

    next_matches = NEXT_MATCHES_RE.search(text)
    if not expected_return and next_matches:
        count = _number(next_matches.group("count"))
        expected_return = _fixture_after_exclusions(fixtures, team_id, published, count) if count else None
        if expected_return:
            date_confidence = "Reported / Estimated"

    return_period = None
    month_match = MONTH_RETURN_RE.search(text)
    if month_match and published:
        month = month_match.group("month")
        month_num = datetime.strptime(month[:3].title(), "%b").month
        year = published.year + (1 if month_num < published.month - 5 else 0)
        return_period = f"{month.title()} {year}"
    elif NEXT_MONTH_RE.search(text) and published:
        first_next_month = (published.replace(day=28) + timedelta(days=4)).replace(day=1)
        return_period = first_next_month.strftime("%B %Y")
    elif re.search(r"\bafter the international break\b", text, re.I):
        return_period = "After the international break (date not established)"

    matchweek = _fixture_return_matchweek(fixtures, team_id, expected_return) if expected_return else None
    explicit_matchweek = MATCH_RETURN_RE.search(text)
    if not expected_return and (return_period or explicit_matchweek or next_matches):
        date_confidence = "Reported / Estimated"
    manager_report = re.search(r"Manager Report:\s*(.+?)(?:\.|$)", text, re.IGNORECASE)
    injury_problem = manager_report.group(1).strip() if manager_report else ""
    injury_problem = re.sub(r"\s*[-–]\s*\d+%.*$", "", injury_problem).strip()
    if not injury_problem:
        first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
        injury_problem = first_sentence[:220]
    injury_problem = re.split(r"\s*[-–]\s*(?:expected\s+back|expected\s+to\s+return|return(?:ing)?\s+(?:on|by)|available\s+(?:on|from|by))\b", injury_problem, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    injury_problem = re.sub(r"\s*[-–]\s*\d+%\s*chance\b.*$", "", injury_problem, flags=re.IGNORECASE).strip()
    injury_problem = re.sub(r"\s*[-–]\s*unknown\s+return\s+date\b.*$", "", injury_problem, flags=re.IGNORECASE).strip()

    return {
        "injury_start_date": injury_start,
        "expected_return_date": expected_return,
        "return_period": return_period,
        "recovery_duration": duration_text,
        "recovery_days": recovery_days,
        "recovery_weeks": recovery_weeks,
        "matchweek": matchweek,
        "estimated": date_confidence == "Reported / Estimated",
        "return_date_confidence": date_confidence,
        "source_date": published,
        "injury_problem": injury_problem,
        "back_in_training": bool(re.search(r"\bback in training\b", text, re.I)),
        "training_update": bool(re.search(r"\breturning to training\b", text, re.I)),
        "late_fitness_test": bool(re.search(r"\blate fitness test\b", text, re.I)),
        "after_international_break": bool(re.search(r"\bafter the international break\b", text, re.I)),
        "explicit_matchweek": int(explicit_matchweek.group("gw")) if explicit_matchweek else None,
        "next_matches_out": _number(NEXT_MATCHES_RE.search(text).group("count")) if NEXT_MATCHES_RE.search(text) else None,
        "available_next_weekend": bool(re.search(r"\bavailable\s+next\s+weekend\b", text, re.I)),
        "fitness_note": (
            "Back in training" if re.search(r"\bback in training\b", text, re.I) else
            "Returning to training" if re.search(r"\breturning to training\b", text, re.I) else
            "Late fitness test" if re.search(r"\blate fitness test\b", text, re.I) else
            "Available next weekend (source wording; date not established)" if re.search(r"\bavailable\s+next\s+weekend\b", text, re.I) else
            "After the international break (date not established)" if re.search(r"\bafter the international break\b", text, re.I) else ""
        ),
        "relative_availability_note": (
            f"Ruled out for the next {_number(NEXT_MATCHES_RE.search(text).group('count'))} matches (date not established)"
            if NEXT_MATCHES_RE.search(text) else ""
        ),
    }


def reconcile_injury_sources(
    candidates: List[Dict[str, Any]], fixtures: Iterable[Any], team_id: int,
    today: Optional[date] = None, stale_after_days: int = 30,
) -> Dict[str, Any]:
    """Preserve disagreements while selecting freshest authoritative evidence for display."""
    today = today or date.today()
    analysed: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for candidate in candidates:
        details = extract_injury_return_info(candidate.get("text", ""), candidate.get("date"), fixtures, team_id, today)
        source_date = details["source_date"]
        details["stale"] = bool(source_date and (today - source_date).days > stale_after_days)
        analysed.append((candidate, details))
    dated = [(source, info) for source, info in analysed if info["expected_return_date"]]
    fresh_dated = [(source, info) for source, info in dated if not info["stale"]]
    pool = fresh_dated or dated
    pool.sort(key=lambda pair: (injury_source_priority(pair[0].get("source", "")), -(pair[1]["source_date"].toordinal() if pair[1]["source_date"] else 0)))
    selected = pool[0] if pool else next(iter(sorted(analysed, key=lambda pair: (injury_source_priority(pair[0].get("source", "")), -(pair[1]["source_date"].toordinal() if pair[1]["source_date"] else 0)))), ({}, {}))
    distinct_dates = {info["expected_return_date"] for _, info in dated}
    return {
        "source": selected[0], "info": selected[1], "sources": analysed,
        "conflict": len(distinct_dates) > 1,
        "stale": selected[1].get("stale", False),
        "last_verified_at": today,
    }
