"""
Phase‑2 dashboard view – shows quick stats and top‑10 players with photos.
"""

from datetime import datetime
from pathlib import Path

import streamlit as st
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine
from config.settings import settings
from ui.components.score_card import render_score_card


def _last_sync_display(stats, raw_data_dir: Path = settings.RAW_DATA_DIR):
    """Return a timestamp only when supported by the sync log or saved FPL caches."""
    recorded_sync = stats.get("last_sync")
    if recorded_sync and recorded_sync != "Never":
        return str(recorded_sync), "Recorded in the ingestion log."

    cache_paths = [raw_data_dir / name for name in ("bootstrap_static.json", "fixtures.json")]
    if all(path.is_file() for path in cache_paths):
        # The cache file times are the only existing sync-adjacent metadata when
        # older runs did not write ingestion_log rows. Label the provenance so
        # this is never mistaken for a logged database-sync completion time.
        cached_at = max(path.stat().st_mtime for path in cache_paths)
        cached_time = datetime.fromtimestamp(cached_at).astimezone()
        cached_label = cached_time.strftime("%d %b %Y")
        note = (
            f"FPL cache files last saved {cached_time.strftime('%d %b %Y, %H:%M')}; "
            "no ingestion-log timestamp is recorded."
        )
        return cached_label, note

    return "Not recorded", "No successful sync timestamp or complete FPL cache pair is available."


def render_phase2_dashboard(repo: FPLRepository, scoring_engine: DecisionScoringEngine):
    st.header("🏆 Dashboard – Overview & Top Players")
    st.caption("Live stats from the configured FPL database with official Premier League player photos.")

    # Summary cards
    stats = repo.get_db_stats()
    last_sync, sync_note = _last_sync_display(stats)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Players", stats.get("total_players", 0))
    col2.metric("Total Teams", stats.get("total_teams", 0))
    col3.metric("Upcoming Fixtures", stats.get("upcoming_fixtures", 0))
    col4.metric("Last Sync", last_sync, help=sync_note)

    st.markdown("---")
    st.subheader("Top 10 Players by Points (with photos)")
    top_players = repo.get_players(sort_by="total_points", limit=10)
    for p in top_players:
        cols = st.columns([1, 4])
        # Photo column – if URL missing, show placeholder
        if p.photo_url:
            cols[0].image(p.photo_url, width=80)
        else:
            cols[0].image("https://via.placeholder.com/80?text=No+Photo")
        # Info column
        with cols[1]:
            st.write(f"**{p.web_name}** – {p.team_short_name or p.team_name}")
            st.write(f"Cost: £{p.cost_float:.1f}m | Points: {p.total_points} | Form: {p.form}")
            # Optional scorecard example using scoring engine
            # score = scoring_engine.evaluate_player(p, [])
            # render_score_card(score, player_name=p.web_name)
    st.markdown("---")
    st.info("Use **Player Stats** in the sidebar for a searchable table, or choose another dashboard page from the sidebar navigation.")
