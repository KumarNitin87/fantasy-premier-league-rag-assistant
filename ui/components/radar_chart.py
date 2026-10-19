"""
UI Component: Interactive Plotly Radar Chart for Player Comparison.
"""

from typing import List
import plotly.graph_objects as go
from src.database.models import Player
from src.analytics.metrics import calculate_xg90, calculate_xa90, calculate_ppm, calculate_avg_fdr
from src.database.repository import FPLRepository


def render_radar_comparison(player1: Player, player2: Player, repo: FPLRepository) -> go.Figure:
    """Generate side-by-side radar chart comparing two players across 6 dimensions."""
    # Metrics list
    categories = [
        "Recent Form",
        "Goal Threat (xG90)",
        "Creativity (xA90)",
        "Minutes Reliability",
        "Fixture Ease",
        "Value (PPM)",
    ]

    # Calculate fixtures
    fx1 = repo.get_upcoming_fixtures(team_id=player1.team_id, limit=3)
    fx2 = repo.get_upcoming_fixtures(team_id=player2.team_id, limit=3)

    fdr1 = calculate_avg_fdr(fx1, player1.team_id)
    fdr2 = calculate_avg_fdr(fx2, player2.team_id)

    # Normalize metrics to 0-100 scale for visual comparability
    # 1. Form (max 12.0)
    p1_form = min(100.0, (player1.form / 10.0) * 100.0)
    p2_form = min(100.0, (player2.form / 10.0) * 100.0)

    # 2. Goal Threat xG90 (max 0.80)
    p1_xg = min(100.0, (calculate_xg90(player1) / 0.60) * 100.0)
    p2_xg = min(100.0, (calculate_xg90(player2) / 0.60) * 100.0)

    # 3. Creativity xA90 (max 0.50)
    p1_xa = min(100.0, (calculate_xa90(player1) / 0.40) * 100.0)
    p2_xa = min(100.0, (calculate_xa90(player2) / 0.40) * 100.0)

    # 4. Minutes Reliability (ratio of minutes to matches * 90)
    p1_mins = min(100.0, (player1.minutes / max(360, player1.minutes)) * 100.0)
    p2_mins = min(100.0, (player2.minutes / max(360, player2.minutes)) * 100.0)

    # 5. Fixture Ease ((5 - FDR) / 4 * 100)
    p1_fdr = max(0.0, min(100.0, ((5.0 - fdr1) / 4.0) * 100.0))
    p2_fdr = max(0.0, min(100.0, ((5.0 - fdr2) / 4.0) * 100.0))

    # 6. Value (PPM / 12 * 100)
    p1_ppm = min(100.0, (calculate_ppm(player1) / 10.0) * 100.0)
    p2_ppm = min(100.0, (calculate_ppm(player2) / 10.0) * 100.0)

    values1 = [p1_form, p1_xg, p1_xa, p1_mins, p1_fdr, p1_ppm]
    values2 = [p2_form, p2_xg, p2_xa, p2_mins, p2_fdr, p2_ppm]

    # Close the radar loop
    values1.append(values1[0])
    values2.append(values2[0])
    radar_categories = categories + [categories[0]]

    fig = go.Figure()

    fig.add_trace(go.Scatterpolar(
        r=values1,
        theta=radar_categories,
        fill="toself",
        name=f"{player1.web_name} ({player1.team_short_name})",
        line_color="#00FF87",
        fillcolor="rgba(0, 255, 135, 0.25)",
    ))

    fig.add_trace(go.Scatterpolar(
        r=values2,
        theta=radar_categories,
        fill="toself",
        name=f"{player2.web_name} ({player2.team_short_name})",
        line_color="#FF1751",
        fillcolor="rgba(255, 23, 81, 0.25)",
    ))

    fig.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100],
                showticklabels=False,
                linecolor="#363c4e",
                gridcolor="#282e3f",
            ),
            angularaxis=dict(
                linecolor="#363c4e",
                gridcolor="#282e3f",
                tickfont=dict(color="#ffffff", size=11),
            ),
            bgcolor="rgba(0,0,0,0)",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.1,
            xanchor="center",
            x=0.5,
            font=dict(color="#ffffff", size=13),
        ),
        margin=dict(l=40, r=40, t=50, b=40),
        height=450,
    )

    return fig
