"""
View 2: Upcoming Fixtures & Fixture Difficulty Rating (FDR) Tracker.
"""

import streamlit as st
import pandas as pd
from src.database.repository import FPLRepository
from config.constants import FDR_COLORS, FDR_LABELS


def _fixture_status(fixture) -> str:
    if fixture.finished_provisional:
        return "Finished · FPL points provisional"
    if fixture.finished:
        return "Final"
    if fixture.started:
        return "Live"
    return "Scheduled"


def _fixture_score(fixture) -> str:
    if fixture.team_h_score is None and fixture.team_a_score is None:
        return "vs"
    home_score = "—" if fixture.team_h_score is None else fixture.team_h_score
    away_score = "—" if fixture.team_a_score is None else fixture.team_a_score
    return f"{home_score} - {away_score}"


def render_fixtures_view(repo: FPLRepository):
    """Render the Fixture Matrix and Gameweek Tracker."""
    st.header("🗓️ Upcoming Fixtures & FDR Difficulty Matrix")
    st.caption("Track match schedules and analyze fixture favorability using Premier League FDR ratings.")

    tab_by_gw, tab_by_club = st.tabs(["Matches by Gameweek", "Fixture Run by Club"])

    with tab_by_gw:
        col_gw, col_sort = st.columns([1, 2])
        with col_gw:
            default_gw = repo.get_current_gameweek() or 6
            gameweek = st.slider("Select Gameweek", min_value=1, max_value=38, value=default_gw)

        fixtures = repo.get_fixtures_by_gameweek(gameweek)
        if not fixtures:
            st.info(f"No fixtures found for Gameweek {gameweek}.")
        else:
            st.markdown(f"### Fixtures for Gameweek {gameweek}")
            cols = st.columns(2)
            for idx, fx in enumerate(fixtures):
                col = cols[idx % 2]
                h_fdr_color = FDR_COLORS.get(fx.team_h_difficulty, "#ccc")
                a_fdr_color = FDR_COLORS.get(fx.team_a_difficulty, "#ccc")
                score_text = _fixture_score(fx)
                fixture_status = _fixture_status(fx)

                with col:
                    st.markdown(
                        f"""
                        <div style="background: #1e222d; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #363c4e;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <div style="flex: 1; text-align: left;">
                                    <strong style="color: #ffffff; font-size: 1.05rem;">{fx.team_h_name}</strong>
                                    <span style="background: {h_fdr_color}; color: #000; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; font-weight: bold; margin-left: 6px;">
                                        FDR {fx.team_h_difficulty}
                                    </span>
                                </div>
                                <div style="padding: 0 10px; color: #8b949e; font-weight: bold;">
                                    {score_text}
                                </div>
                                <div style="flex: 1; text-align: right;">
                                    <span style="background: {a_fdr_color}; color: #000; font-size: 0.75rem; padding: 2px 6px; border-radius: 4px; font-weight: bold; margin-right: 6px;">
                                        FDR {fx.team_a_difficulty}
                                    </span>
                                    <strong style="color: #ffffff; font-size: 1.05rem;">{fx.team_a_name}</strong>
                                </div>
                            </div>
                            <div style="color: #8b949e; font-size: 0.75rem; margin-top: 6px; text-align: center;">
                                {fixture_status} · Kickoff: {fx.kickoff_time or 'TBD'}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

    with tab_by_club:
        teams = repo.get_all_teams()
        selected_club_name = st.selectbox("Select Club to view upcoming 5 matches:", [t.name for t in teams])
        selected_club = next((t for t in teams if t.name == selected_club_name), None)

        if selected_club:
            upcoming = repo.get_upcoming_fixtures(team_id=selected_club.id, limit=5)
            if not upcoming:
                st.info("No upcoming fixtures scheduled.")
            else:
                table_rows = []
                for fx in upcoming:
                    is_h = fx.team_h == selected_club.id
                    opp = fx.team_a_name if is_h else fx.team_h_name
                    loc = "Home (H)" if is_h else "Away (A)"
                    fdr = fx.team_h_difficulty if is_h else fx.team_a_difficulty
                    table_rows.append({
                        "Gameweek": f"GW{fx.event}",
                        "Opponent": opp,
                        "Venue": loc,
                        "FDR Difficulty": fdr,
                        "FDR Description": FDR_LABELS.get(fdr, "Neutral"),
                        "Kickoff": fx.kickoff_time,
                    })

                st.table(pd.DataFrame(table_rows))
