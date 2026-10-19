"""
View 1: Player Statistics Explorer & Interactive Filter Table.
"""

import streamlit as st
import pandas as pd
from src.database.repository import FPLRepository
from src.analytics.scoring import DecisionScoringEngine
from ui.components.player_photo import resolve_player_photo_urls
from ui.components.player_profile import render_player_profile
from ui.components.score_card import render_score_card


def render_stats_explorer(repo: FPLRepository, scoring_engine: DecisionScoringEngine):
    """Render the Player Statistics Explorer page."""
    st.header("📊 Premier League & FPL Player Statistics Explorer")
    st.caption("Official FPL stats from the latest synchronization. GW points reflect the current API value and may change before finalization.")

    # Filter Controls
    col_pos, col_team, col_price, col_status = st.columns(4)

    with col_pos:
        pos_filter = st.selectbox("Position", ["All Positions", "GKP", "DEF", "MID", "FWD"])
    with col_team:
        teams = repo.get_all_teams()
        team_options = ["All Teams"] + [t.name for t in teams]
        team_filter = st.selectbox("Club", team_options)
    with col_price:
        max_cost = st.slider("Max Price (£m)", min_value=4.0, max_value=16.0, value=16.0, step=0.5)
    with col_status:
        status_filter = st.selectbox("Availability", ["All Statuses", "Available (Fit)", "Injured / Doubtful"])

    search_query = st.text_input("🔍 Quick Player Search (by name)", placeholder="e.g. Haaland, Saka, Palmer, Salah...")

    # Fetch matching players
    selected_pos = None if pos_filter == "All Positions" else pos_filter
    selected_team_id = None
    if team_filter != "All Teams":
        matched_team = next((t for t in teams if t.name == team_filter), None)
        if matched_team:
            selected_team_id = matched_team.id

    selected_status = None
    if status_filter == "Available (Fit)":
        selected_status = "a"

    if search_query.strip():
        players = repo.search_players(search_query.strip(), limit=50)
        # Apply the visible filters to search results too, so search and filters
        # continue to describe the same result set.
        players = [
            p for p in players
            if (selected_pos is None or p.position == selected_pos)
            and (selected_team_id is None or p.team_id == selected_team_id)
            and p.cost_float <= max_cost
            and (selected_status is None or p.status == selected_status)
        ]
    else:
        players = repo.get_players(
            position=selected_pos,
            team_id=selected_team_id,
            max_price=max_cost,
            status=selected_status,
            sort_by="total_points",
            limit=150,
        )

    if status_filter == "Injured / Doubtful":
        players = [p for p in players if p.status in {"d", "i", "s", "u"}]

    result_word = "player" if len(players) == 1 else "players"
    result_context = "matching your search" if search_query.strip() else "matching criteria"
    st.write(f"Showing **{len(players)}** {result_word} {result_context}")

    if not players:
        if search_query.strip():
            st.warning("No players found matching your search.")
        else:
            st.warning("No players found matching current filters.")
        return

    photo_urls = resolve_player_photo_urls(players, repo)

    # Build DataFrame for interactive table. Photo URLs use the player's existing
    # FPL code via the same URL builder used during ingestion.
    table_data = []
    for p in players:
        status_symbol = "🟢 Fit" if p.status == "a" else f"⚠️ {p.status.upper()} ({p.chance_of_playing_next_round or 0}%)"
        table_data.append({
            "Photo": photo_urls.get(p.id),
            "ID": p.id,
            "Name": p.web_name,
            "Club": p.team_short_name or p.team_name,
            "Pos": p.position,
            "Cost": f"£{p.cost_float:.1f}m",
            "Form": p.form,
            "GW Points (API)": p.event_points,
            "Points": p.total_points,
            "Goals": p.goals_scored,
            "Assists": p.assists,
            "Clean Sheets": p.clean_sheets,
            "xG": round(p.expected_goals, 2),
            "xA": round(p.expected_assists, 2),
            "ICT Index": p.ict_index,
            "Status": status_symbol,
        })

    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        column_config={
            "Photo": st.column_config.ImageColumn("Photo", width="small"),
            "ID": st.column_config.NumberColumn(format="%d"),
            "Points": st.column_config.ProgressColumn(min_value=0, max_value=60, format="%d"),
            "Form": st.column_config.NumberColumn(format="%.1f"),
        },
        hide_index=True,
        use_container_width=True,
    )

    # Keep the compact table. A unique match opens its profile automatically;
    # ambiguous matches require an explicit single-player selection first.
    selected_profile_player = None
    if search_query.strip() and len(players) == 1:
        selected_profile_player = players[0]
    elif search_query.strip() and len(players) > 1:
        profile_options = ["Select one player to view the detailed profile"]
        profile_by_option = {}
        for player in players:
            label = (
                f"{player.full_name or player.web_name} "
                f"({player.team_short_name or player.team_name}) · {player.position} · ID {player.id}"
            )
            profile_options.append(label)
            profile_by_option[label] = player
        profile_choice = st.selectbox(
            "Choose one search result for a detailed profile",
            profile_options,
            key=f"player_profile_choice_{search_query.strip().lower()}",
        )
        selected_profile_player = profile_by_option.get(profile_choice)

    if selected_profile_player:
        render_player_profile(
            selected_profile_player,
            photo_urls.get(selected_profile_player.id),
            repo,
        )

    # Preserve the existing deterministic scorecard and full history inspector.
    # A multi-match search remains a compact table until the user narrows it.
    if search_query.strip() and len(players) > 1 and selected_profile_player is None:
        return

    st.markdown("---")
    st.subheader("🔎 Player In-Depth Spotlight & Confidence Analysis")
    player_names = [f"{p.web_name} ({p.team_short_name or p.team_name}) - ID:{p.id}" for p in players]
    selected_item = st.selectbox("Select player to inspect detailed decision scorecard:", player_names)

    if selected_item:
        p_id = int(selected_item.split("ID:")[-1])
        inspected_player = repo.get_player_by_id(p_id)
        if inspected_player:
            fxs = repo.get_upcoming_fixtures(team_id=inspected_player.team_id, limit=3)
            score = scoring_engine.evaluate_player(inspected_player, fxs)
            render_score_card(score, player_name=f"{inspected_player.web_name} ({inspected_player.full_name})")

            # Historical gameweeks if available
            history = repo.get_player_gameweek_history(inspected_player.id)
            if history:
                st.markdown("**📈 Gameweek Points Progression:**")
                chart_df = pd.DataFrame([
                    {"Gameweek": f"GW{h.round}", "Points": h.total_points, "xG": h.expected_goals, "xA": h.expected_assists}
                    for h in history
                ])
                st.line_chart(chart_df.set_index("Gameweek")[["Points", "xG", "xA"]])
