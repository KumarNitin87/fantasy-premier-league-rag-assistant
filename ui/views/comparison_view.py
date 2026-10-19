"""
View 3: Head-to-Head Player Comparison & Radar Chart Analytics.
"""

import streamlit as st
import pandas as pd
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine
from src.analytics.metrics import calculate_xgi90, calculate_xg90, calculate_xa90, calculate_ppm, calculate_avg_fdr
from ui.components.radar_chart import render_radar_comparison
from ui.components.score_card import render_score_card


def _comparison_dataframe(metrics_data):
    """Normalize mixed metric values for reliable Streamlit/Arrow serialization."""
    return pd.DataFrame(metrics_data).fillna("").astype(str)


def render_comparison_view(repo: FPLRepository, scoring_engine: DecisionScoringEngine):
    """Render the Player Comparison view."""
    st.header("⚔️ Head-to-Head Player Comparison Studio")
    st.caption("Compare two players across underlying metrics, expected data, fixture schedules, and confidence scores.")

    all_players = repo.get_players(limit=500, sort_by="total_points")
    player_options = {f"{p.web_name} ({p.team_short_name or p.team_name}) - £{p.cost_float}m [ID:{p.id}]": p.id for p in all_players}
    options_list = list(player_options.keys())

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        default_idx1 = 0 if len(options_list) > 0 else 0
        sel1 = st.selectbox("Select Player 1:", options_list, index=default_idx1)
    with col_p2:
        default_idx2 = 1 if len(options_list) > 1 else 0
        sel2 = st.selectbox("Select Player 2:", options_list, index=default_idx2)

    if not sel1 or not sel2:
        return

    p1_id = player_options[sel1]
    p2_id = player_options[sel2]

    p1 = repo.get_player_by_id(p1_id)
    p2 = repo.get_player_by_id(p2_id)

    if not p1 or not p2:
        st.error("Error loading selected players.")
        return

    # 1. Radar Chart Comparison
    st.markdown("### 🕸️ Multi-Dimensional Tactical Radar")
    fig = render_radar_comparison(p1, p2, repo)
    st.plotly_chart(fig, use_container_width=True)

    # 2. Side-by-Side Metric Comparison Table
    st.markdown("### 📊 Head-to-Head Metric Breakdown")
    fx1 = repo.get_upcoming_fixtures(team_id=p1.team_id, limit=3)
    fx2 = repo.get_upcoming_fixtures(team_id=p2.team_id, limit=3)
    fdr1 = calculate_avg_fdr(fx1, p1.team_id)
    fdr2 = calculate_avg_fdr(fx2, p2.team_id)

    metrics_data = [
        {"Metric": "Club", p1.web_name: p1.team_name, p2.web_name: p2.team_name},
        {"Metric": "Position", p1.web_name: p1.position, p2.web_name: p2.position},
        {"Metric": "Current Cost", p1.web_name: f"£{p1.cost_float}m", p2.web_name: f"£{p2.cost_float}m"},
        {"Metric": "Recent Form", p1.web_name: p1.form, p2.web_name: p2.form},
        {"Metric": "Total Points", p1.web_name: p1.total_points, p2.web_name: p2.total_points},
        {"Metric": "Goals Scored", p1.web_name: p1.goals_scored, p2.web_name: p2.goals_scored},
        {"Metric": "Assists", p1.web_name: p1.assists, p2.web_name: p2.assists},
        {"Metric": "Expected Goals (xG)", p1.web_name: round(p1.expected_goals, 2), p2.web_name: round(p2.expected_goals, 2)},
        {"Metric": "Expected Assists (xA)", p1.web_name: round(p1.expected_assists, 2), p2.web_name: round(p2.expected_assists, 2)},
        {"Metric": "xGI per 90 mins", p1.web_name: calculate_xgi90(p1), p2.web_name: calculate_xgi90(p2)},
        {"Metric": "Points Per Million (PPM)", p1.web_name: calculate_ppm(p1), p2.web_name: calculate_ppm(p2)},
        {"Metric": "Upcoming 3-GW Avg FDR", p1.web_name: f"{fdr1:.1f} (Lower = Easier)", p2.web_name: f"{fdr2:.1f} (Lower = Easier)"},
        {"Metric": "Official Status", p1.web_name: f"{p1.status.upper()} ({p1.chance_of_playing_next_round or 100}%)", p2.web_name: f"{p2.status.upper()} ({p2.chance_of_playing_next_round or 100}%)"},
    ]

    df_comp = _comparison_dataframe(metrics_data)
    st.table(df_comp.set_index("Metric"))

    # 3. Decision Confidence Scores
    st.markdown("### 🎯 Decision Confidence Comparison")
    score1 = scoring_engine.evaluate_player(p1, fx1)
    score2 = scoring_engine.evaluate_player(p2, fx2)

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        render_score_card(score1, player_name=p1.web_name, responsive_metrics=True)
    with col_s2:
        render_score_card(score2, player_name=p2.web_name, responsive_metrics=True)
