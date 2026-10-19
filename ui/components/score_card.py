"""
UI Component: Decision Confidence Score Badge and Gauge Card.
"""

from html import escape

import streamlit as st
from src.analytics.scoring import ScoreBreakdown


def render_score_card(
    score: ScoreBreakdown,
    player_name: str = "",
    responsive_metrics: bool = False,
):
    """Render a styled visual scorecard for a player's decision confidence."""
    st.markdown(
        f"""
        <div style="background-color: #1e222d; padding: 18px; border-radius: 10px; border: 1px solid #363c4e; margin-bottom: 15px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="margin: 0; color: #ffffff;">{player_name} Decision Confidence</h3>
                    <span style="background-color: {score.tier_color}; color: #000000; padding: 4px 10px; border-radius: 12px; font-weight: bold; font-size: 0.85rem;">
                        {score.tier}
                    </span>
                </div>
                <div style="text-align: right;">
                    <span style="font-size: 2.2rem; font-weight: 800; color: {score.tier_color};">{score.final_score}</span>
                    <span style="color: #8b949e; font-size: 1.1rem;"> / 100</span>
                </div>
            </div>
            <p style="color: #c9d1d9; margin-top: 10px; font-size: 0.95rem;">{score.summary_verdict}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    team_delta = score.subscores.get("team_delta")
    team_delta_display = f"{team_delta}%" if team_delta is not None else "N/A"
    metrics = (
        ("Recent Form", f"{score.subscores.get('form', 0)}%"),
        ("Fixture Ease", f"{score.subscores.get('fixture', 0)}%"),
        ("Underlying xGI", f"{score.subscores.get('underlying', 0)}%"),
        ("Value (PPM)", f"{score.subscores.get('value', 0)}%"),
        ("Team Delta", team_delta_display),
    )

    if responsive_metrics:
        # CSS Grid uses the available width of the score card. At narrower
        # widths it wraps cards into additional rows rather than clipping text.
        metric_cards = "".join(
            f'<div class="decision-score-metric-card">'
            f'<div class="decision-score-metric-label">{escape(label)}</div>'
            f'<div class="decision-score-metric-value">{escape(value)}</div>'
            "</div>"
            for label, value in metrics
        )
        st.markdown(
            """
            <style>
                .decision-score-metrics-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(min(100%, 132px), 1fr));
                    gap: 0.55rem;
                    width: 100%;
                    align-items: stretch;
                }
                .decision-score-metric-card {
                    box-sizing: border-box;
                    min-width: 0;
                    min-height: 76px;
                    padding: 0.65rem 0.55rem;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: center;
                    gap: 0.25rem;
                    text-align: center;
                    border: 1px solid rgba(167, 225, 190, 0.14);
                    border-radius: 10px;
                    background: rgba(6, 17, 13, 0.58);
                }
                .decision-score-metric-label {
                    max-width: 100%;
                    color: #d7e2dc;
                    font-size: 0.82rem;
                    line-height: 1.25;
                    white-space: normal;
                    overflow-wrap: anywhere;
                    text-overflow: clip;
                }
                .decision-score-metric-value {
                    color: #00ff87;
                    font-size: clamp(1.1rem, 1.5vw, 1.4rem);
                    font-weight: 700;
                    line-height: 1.2;
                    white-space: nowrap;
                    overflow: visible;
                }
                @media (max-width: 480px) {
                    .decision-score-metrics-grid {
                        grid-template-columns: repeat(auto-fit, minmax(min(100%, 112px), 1fr));
                    }
                    .decision-score-metric-value {
                        font-size: 1.1rem;
                    }
                }
            </style>
            """
            + f'<div class="decision-score-metrics-grid">{metric_cards}</div>',
            unsafe_allow_html=True,
        )
    else:
        # Keep the original compact presentation on pages outside the requested
        # Comparison and Transfer views.
        c1, c2, c3, c4, c5 = st.columns(5)
        for column, (label, value) in zip((c1, c2, c3, c4, c5), metrics):
            with column:
                st.metric(label, value)

    # Expandable details
    with st.expander("🔍 View Scoring Reasoning & Applied Penalties"):
        col_pos, col_neg = st.columns(2)
        with col_pos:
            st.markdown("**✅ Positive Drivers:**")
            if score.positive_reasons:
                for r in score.positive_reasons:
                    st.markdown(f"- {r}")
            else:
                st.markdown("- No dominant positive factors.")

        with col_neg:
            st.markdown("**⚠️ Risk Factors & Penalties:**")
            if score.penalties_applied:
                for p in score.penalties_applied:
                    st.markdown(f"- **{p['factor']}**: {p['reason']} *(multiplier: {p['multiplier']}x)*")
            if score.negative_reasons:
                for r in score.negative_reasons:
                    st.markdown(f"- {r}")
            if not score.penalties_applied and not score.negative_reasons:
                st.markdown("- No active penalties.")
