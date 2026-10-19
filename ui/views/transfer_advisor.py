"""
View 4: Transfer Decision Studio & Captaincy Recommendation Predictor.
"""

import streamlit as st
import pandas as pd
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine
from src.analytics.transfer_eval import TransferAnalyzer
from src.analytics.captain_picker import CaptainPicker
from ui.components.score_card import render_score_card


def render_transfer_advisor(
    repo: FPLRepository,
    scoring_engine: DecisionScoringEngine,
    transfer_analyzer: TransferAnalyzer,
    captain_picker: CaptainPicker,
):
    """Render the Transfer and Captaincy Advisor page."""
    st.header("🔄 Transfer Studio & Captaincy Decision Advisor")
    st.caption("Evaluate player transfers and discover mathematically optimized captain options with confidence scores.")

    tab_transfers, tab_captains = st.tabs(["Transfer Decision Evaluator", "Captaincy Predictor (MCDA)"])

    # =========================================================================
    # Tab 1: Transfer Evaluator
    # =========================================================================
    with tab_transfers:
        st.markdown("### 🔁 Transfer Evaluator: Player OUT vs Player IN")
        st.write("Compare the risk/reward of dropping a current player for an incoming target.")

        all_players = repo.get_players(limit=500, sort_by="total_points")
        player_dict = {f"{p.web_name} ({p.team_short_name or p.team_name}) - £{p.cost_float}m [ID:{p.id}]": p.id for p in all_players}
        names_list = list(player_dict.keys())

        col_out, col_in, col_bank = st.columns([2, 2, 1])
        with col_out:
            sel_out = st.selectbox("Player OUT (Selling)", names_list, index=0)
        with col_in:
            sel_in = st.selectbox("Player IN (Buying)", names_list, index=1 if len(names_list) > 1 else 0)
        with col_bank:
            bank = st.number_input("Money in Bank (£m)", min_value=0.0, max_value=20.0, value=0.5, step=0.1)

        if sel_out and sel_in:
            p_out = repo.get_player_by_id(player_dict[sel_out])
            p_in = repo.get_player_by_id(player_dict[sel_in])

            if p_out and p_in:
                if p_out.id == p_in.id:
                    st.warning("Please select two different players for transfer evaluation.")
                else:
                    eval_result = transfer_analyzer.evaluate(p_out, p_in, bank_balance=bank)

                    # Verdict Banner
                    st.markdown(
                        f"""
                        <div style="background-color: #1e222d; padding: 20px; border-radius: 10px; border: 2px solid {eval_result.verdict_badge_color}; margin: 20px 0;">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <div>
                                    <h4 style="margin: 0; color: #8b949e;">TRANSFER VERDICT</h4>
                                    <h2 style="margin: 4px 0 0 0; color: {eval_result.verdict_badge_color};">{eval_result.verdict}</h2>
                                </div>
                                <div style="text-align: right;">
                                    <div style="font-size: 0.9rem; color: #8b949e;">Confidence Delta</div>
                                    <div style="font-size: 2rem; font-weight: bold; color: {eval_result.verdict_badge_color};">
                                        {'+' if eval_result.confidence_delta > 0 else ''}{eval_result.confidence_delta} pts
                                    </div>
                                </div>
                            </div>
                            <div style="margin-top: 12px; color: #ffffff; font-size: 0.95rem;">
                                Cost Impact: <strong>£{eval_result.cost_diff:+.1f}m</strong> | Bank Available: <strong>£{bank:.1f}m</strong> | Affordable: <strong>{'Yes ✅' if eval_result.is_affordable else 'No ❌'}</strong>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # Rationale List
                    st.markdown("**📋 Analytical Transfer Drivers:**")
                    for r in eval_result.rationale:
                        st.markdown(f"- {r}")

                    # Compare Individual Scores
                    col_s_out, col_s_in = st.columns(2)
                    with col_s_out:
                        render_score_card(
                            eval_result.score_out,
                            player_name=f"Player OUT: {p_out.web_name}",
                            responsive_metrics=True,
                        )
                    with col_s_in:
                        render_score_card(
                            eval_result.score_in,
                            player_name=f"Player IN: {p_in.web_name}",
                            responsive_metrics=True,
                        )

    # =========================================================================
    # Tab 2: Captaincy Predictor
    # =========================================================================
    with tab_captains:
        st.markdown("### 👑 Gameweek Captaincy Optimizer (MCDA)")
        st.write("Rankings computed using multi-criteria decision analysis: form, fixture difficulty, home advantage, and attacking ceiling.")

        with st.spinner("Calculating optimal captaincy candidates..."):
            captains = captain_picker.get_top_captains(top_n=5)

        for cap in captains:
            p = cap.player
            loc_label = "Home (H)" if cap.is_home else "Away (A)"
            st.markdown(
                f"""
                <div style="background-color: #1e222d; padding: 16px 20px; border-radius: 8px; border-left: 6px solid {cap.badge_color}; margin-bottom: 14px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-size: 1.2rem; font-weight: bold; color: #ffffff;">#{cap.rank} {p.web_name}</span>
                            <span style="color: #8b949e; font-size: 0.95rem; margin-left: 8px;">({p.team_short_name or p.team_name} | {p.position})</span>
                            <div style="margin-top: 4px;">
                                <span style="background: {cap.badge_color}; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: bold;">
                                    {cap.tier_label}
                                </span>
                                <span style="color: #c9d1d9; font-size: 0.85rem; margin-left: 10px;">
                                    Next Match: <strong>vs {cap.next_opponent} ({loc_label}) [FDR {cap.fdr}]</strong>
                                </span>
                            </div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size: 0.8rem; color: #8b949e;">Captaincy Index</div>
                            <div style="font-size: 1.8rem; font-weight: 800; color: {cap.badge_color};">{cap.captain_index}</div>
                        </div>
                    </div>
                    <div style="margin-top: 10px; font-size: 0.9rem; color: #e6edf3; border-top: 1px solid #30363d; padding-top: 8px;">
                        💡 <strong>Tactical Rationale:</strong> {cap.rationale}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
