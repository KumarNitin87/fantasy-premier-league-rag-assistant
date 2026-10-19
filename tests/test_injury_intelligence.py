from datetime import date
from types import SimpleNamespace

from src.database.models import Fixture
from src.rag.injury_intelligence import (
    extract_injury_return_info,
    injury_source_priority,
    reconcile_injury_sources,
)
from src.api.rss_client import RSSNewsClient


def _fixtures():
    return [
        Fixture(id=1, event=8, team_h=10, team_a=2, kickoff_time="2026-10-10T15:00:00Z"),
        Fixture(id=2, event=9, team_h=3, team_a=10, kickoff_time="2026-10-17T15:00:00Z"),
        Fixture(id=3, event=10, team_h=10, team_a=8, kickoff_time="2026-10-24T15:00:00Z"),
        Fixture(id=4, event=11, team_h=1, team_a=10, kickoff_time="2026-10-31T15:00:00Z"),
    ]


def test_exact_injury_start_and_explicit_return_date_are_preserved():
    info = extract_injury_return_info(
        "Havertz injured on 24 September 2026. Expected back 14 October 2026.",
        "2026-09-28T08:00:00Z", _fixtures(), 10, today=date(2026, 10, 1),
    )
    assert info["injury_start_date"] == date(2026, 9, 24)
    assert info["expected_return_date"] == date(2026, 10, 14)
    assert info["return_date_confidence"] == "Reported / Estimated"
    assert info["matchweek"] == 9


def test_explicit_confirmed_return_is_distinguished():
    info = extract_injury_return_info(
        "Player confirmed available on 12 October 2026.", "2026-10-01", [], 10,
    )
    assert info["expected_return_date"] == date(2026, 10, 12)
    assert info["return_date_confidence"] == "Confirmed"


def test_duration_uses_source_injury_start_and_is_estimated():
    info = extract_injury_return_info(
        "Injured on 24 September 2026 and ruled out for three weeks.",
        "2026-09-28", _fixtures(), 10,
    )
    assert info["expected_return_date"] == date(2026, 10, 15)
    assert info["recovery_days"] == (21, 21)
    assert info["recovery_weeks"] == (3.0, 3.0)
    assert info["return_date_confidence"] == "Reported / Estimated"


def test_duration_without_start_uses_known_publication_date_only():
    info = extract_injury_return_info("Out for 2 weeks with an ankle injury.", "2026-09-28", [], 10)
    assert info["expected_return_date"] == date(2026, 10, 12)
    unknown = extract_injury_return_info("Out for two weeks.", "", [], 10)
    assert unknown["expected_return_date"] is None
    assert unknown["recovery_days"] == (14, 14)


def test_rss_record_without_source_publication_date_stays_unknown():
    article = RSSNewsClient._normalize_entry(SimpleNamespace(title="Injury update", summary="Out for three weeks", link=""), "BBC Sport")
    assert article["published_at"] == ""
    info = extract_injury_return_info(article["summary"], article["published_at"], [], 10)
    assert info["expected_return_date"] is None


def test_after_international_break_is_not_converted_to_an_invented_date():
    info = extract_injury_return_info("Could return after the international break.", "2026-09-28", [], 10)
    assert info["after_international_break"] is True
    assert info["return_period"] == "After the international break (date not established)"
    assert info["expected_return_date"] is None


def test_month_only_return_is_a_period_not_an_exact_date():
    info = extract_injury_return_info("Expected back in October.", "2026-09-28", [], 10)
    assert info["return_period"] == "October 2026"
    assert info["expected_return_date"] is None


def test_return_matchweek_uses_club_fixture_database():
    info = extract_injury_return_info(
        "Expected to return on 12 October 2026.", "2026-09-28", _fixtures(), 10,
    )
    assert info["matchweek"] == 9


def test_next_three_matches_return_uses_fixture_schedule_as_an_estimate():
    info = extract_injury_return_info(
        "Ruled out for the next three matches.", "2026-10-01", _fixtures(), 10,
    )
    assert info["expected_return_date"] == date(2026, 10, 31)
    assert info["matchweek"] == 11
    assert info["return_date_confidence"] == "Reported / Estimated"


def test_source_attribution_and_priority_are_preserved():
    records = [
        {"source": "Premier League Official Medical Bulletin", "date": "2026-09-30", "text": "Expected back 20 October 2026.", "link": ""},
        {"source": "Official Arsenal Club Medical Update", "date": "2026-09-28", "text": "Expected back 14 October 2026.", "link": "https://arsenal.example/injury"},
    ]
    result = reconcile_injury_sources(records, _fixtures(), 10, today=date(2026, 10, 1))
    assert result["source"]["source"] == "Official Arsenal Club Medical Update"
    assert result["source"]["link"] == "https://arsenal.example/injury"
    assert result["conflict"] is True
    assert len(result["sources"]) == 2
    assert injury_source_priority("Transfermarkt Injury History") < injury_source_priority("BBC Sport Football")


def test_fresh_trusted_information_precedes_stale_higher_priority_record():
    records = [
        {"source": "Official Arsenal Club Medical Update", "date": "2026-07-01", "text": "Expected back 12 October 2026."},
        {"source": "BBC Sport Football", "date": "2026-09-30", "text": "Expected back 20 October 2026."},
    ]
    result = reconcile_injury_sources(records, _fixtures(), 10, today=date(2026, 10, 1))
    assert result["source"]["source"] == "BBC Sport Football"
    assert result["stale"] is False
    assert result["conflict"] is True


def test_unknown_return_is_never_inferred_from_status_or_injury_type():
    info = extract_injury_return_info(
        "Manager Report: Hamstring injury - 75% chance of playing.",
        "2026-09-28", _fixtures(), 10, today=date(2026, 10, 1),
    )
    assert info["expected_return_date"] is None
    assert info["recovery_days"] is None
    assert info["matchweek"] is None
    assert info["return_date_confidence"] == "Unknown"


def test_sources_without_return_dates_remain_available_as_evidence():
    result = reconcile_injury_sources([
        {"source": "Premier League Official Medical Bulletin", "date": "2026-09-28", "text": "Doubtful with a knee problem."}
    ], _fixtures(), 10, today=date(2026, 10, 1))
    assert result["info"]["expected_return_date"] is None
    assert result["source"]["text"] == "Doubtful with a knee problem."


def test_injury_card_render_path_does_not_dump_raw_bulletin_into_visible_body():
    # Regression guard: injury cards use the structured branch; raw article text is only rendered
    # inside the evidence expander. Non-injury news retains the ordinary article rendering path.
    from pathlib import Path

    view = Path("ui/views/news_feed.py").read_text(encoding="utf-8")
    assert "if not (linked_player and is_injury_record):\n                    st.write(clean_text)" in view
    assert 'with st.expander("View source evidence")' in view


def test_source_evidence_html_is_sanitized_before_display():
    from ui.views.news_feed import injury_severity_key, sanitize_text

    assert sanitize_text("<b>Hamstring injury</b> &amp; expected back") == "Hamstring injury & expected back"
    assert injury_severity_key("d") == "doubtful"
    assert injury_severity_key("i") == "out"
