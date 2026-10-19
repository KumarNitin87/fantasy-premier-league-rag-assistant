"""Data helpers and presentation for the Player Stats single-player profile."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.constants import FDR_COLORS
from src.database.models import Fixture, Player, PlayerGameweekHistory
from src.database.repository import FPLRepository


POSITION_LABELS = {
    "GKP": "Goalkeeper",
    "DEF": "Defender",
    "MID": "Midfielder",
    "FWD": "Forward",
}


def availability_label(player: Player) -> Tuple[str, str]:
    """Return a user-facing label and icon from the stored FPL availability code."""
    status = (player.status or "").lower()
    chance = player.chance_of_playing_next_round
    if status == "a":
        return "Available", "🟢"
    if status == "d":
        detail = f" ({chance}% chance of playing)" if chance is not None else ""
        return f"Doubtful{detail}", "🟡"
    if status == "i":
        return "Injured", "🔴"
    if status == "s":
        return "Suspended", "⚫"
    if status == "u":
        return "Unavailable", "⚫"
    return "Not available", "⚪"


def format_profile_value(value: Any, digits: int = 2) -> str:
    if value is None or value == "":
        return "N/A"
    if isinstance(value, bool):
        return "N/A"
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
        return f"{numeric:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def player_metric_cards(player: Player) -> List[Tuple[str, Any]]:
    """Build profile metrics from the existing Player record, including role metrics."""
    metrics: List[Tuple[str, Any]] = [
        ("Gameweek Points (API)", player.event_points),
        ("Total Points", player.total_points),
        ("Goals", player.goals_scored),
        ("Assists", player.assists),
        ("Clean Sheets", player.clean_sheets),
        ("xG", player.expected_goals),
        ("xA", player.expected_assists),
        ("ICT Index", player.ict_index),
    ]
    if player.position == "GKP":
        metrics.extend([
            ("Saves", player.saves),
            ("Penalties Saved", player.penalties_saved),
            ("Goals Conceded", player.goals_conceded),
        ])
    elif player.position == "DEF":
        metrics.extend([
            ("Goals Conceded", player.goals_conceded),
            ("xGC", player.expected_goals_conceded),
            ("BPS", player.bps),
        ])
    elif player.position in {"MID", "FWD"}:
        metrics.extend([
            ("xGI", player.expected_goal_involvements),
            ("Bonus", player.bonus),
        ])
    return metrics


def profile_header_values(player: Player) -> Dict[str, Any]:
    """Expose the header's displayed values directly from one player record."""
    status, icon = availability_label(player)
    return {
        "name": player.full_name.strip() or player.web_name,
        "team": player.team_name or player.team_short_name or "Club unavailable",
        "position": POSITION_LABELS.get(player.position, "Position unavailable"),
        "position_code": player.position or "N/A",
        "player_id": player.id,
        "price": player.cost_float if player.now_cost is not None else None,
        "form": player.form,
        "total_points": player.total_points,
        "selected_by_percent": player.selected_by_percent,
        "availability": status,
        "availability_icon": icon,
    }


def latest_gameweek_rows(
    history: Iterable[PlayerGameweekHistory], limit: int = 5
) -> List[Dict[str, Any]]:
    """Return the latest available history rows in chronological chart order."""
    rows = sorted(history, key=lambda item: item.round)[-max(0, limit):]
    return [
        {
            "Gameweek": f"GW{item.round}{' *' if item.is_provisional else ''}",
            "Points": item.total_points,
        }
        for item in rows
    ]


def upcoming_fixture_rows(player: Player, fixtures: Iterable[Fixture]) -> List[Dict[str, Any]]:
    """Adapt repository fixtures into display rows without deriving any FPL values."""
    rows: List[Dict[str, Any]] = []
    for fixture in fixtures:
        is_home = fixture.team_h == player.team_id
        if not is_home and fixture.team_a != player.team_id:
            continue
        opponent = (
            fixture.team_a_short_name or fixture.team_a_name
            if is_home
            else fixture.team_h_short_name or fixture.team_h_name
        )
        fdr = fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
        rows.append({
            "Gameweek": f"GW{fixture.event}" if fixture.event is not None else "N/A",
            "Opponent": opponent or "N/A",
            "Venue": "Home (H)" if is_home else "Away (A)",
            "FDR": fdr,
            "Date": _format_kickoff(fixture.kickoff_time),
        })
    return rows


def _format_kickoff(value: Optional[str]) -> str:
    if not value:
        return "Date unavailable"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%a, %d %b %Y")
    except (TypeError, ValueError):
        return "Date unavailable"


def profile_data(repo: FPLRepository, player: Player) -> Dict[str, Any]:
    """Retrieve the existing history and upcoming fixture rows for one player."""
    history = repo.get_player_gameweek_history(player.id, limit=5)
    fixtures = repo.get_upcoming_fixtures(team_id=player.team_id, limit=5)
    return {
        "history": latest_gameweek_rows(history, limit=5),
        "fixtures": upcoming_fixture_rows(player, fixtures),
    }


def per90_profile_metrics(player: Player) -> List[Tuple[str, float, float]]:
    """Return available position-relevant per-90 values and visualization scales.

    The metric values come only from fields on the existing player record. The final
    tuple value is a display scale used to make the radar's different units legible.
    """
    minutes = player.minutes or 0
    if minutes <= 0:
        return []

    if player.position == "GKP":
        raw_metrics = [
            ("Saves / 90", player.saves, 6.0),
            ("Clean sheets / 90", player.clean_sheets, 0.6),
            ("Goals conceded / 90", player.goals_conceded, 4.0),
            ("xGC / 90", player.expected_goals_conceded, 3.0),
        ]
    elif player.position == "DEF":
        raw_metrics = [
            ("Clean sheets / 90", player.clean_sheets, 0.6),
            ("Goals / 90", player.goals_scored, 0.9),
            ("Assists / 90", player.assists, 0.6),
            ("xGC / 90", player.expected_goals_conceded, 2.5),
            ("BPS / 90", player.bps, 35.0),
        ]
    else:
        raw_metrics = [
            ("Goals / 90", player.goals_scored, 0.9),
            ("Assists / 90", player.assists, 0.6),
            ("xG / 90", player.expected_goals, 0.8),
            ("xA / 90", player.expected_assists, 0.6),
            ("xGI / 90", player.expected_goal_involvements, 1.2),
            ("ICT / 90", player.ict_index, 12.0),
        ]

    return [
        (label, float(value) * 90.0 / minutes, scale)
        for label, value, scale in raw_metrics
        if value is not None
    ]


def render_player_profile(player: Player, photo_url: str, repo: FPLRepository) -> None:
    """Render a detailed, data-backed analytics profile for a single player."""
    # Keep Streamlit/chart dependencies out of the testable data helpers.
    import pandas as pd
    import plotly.graph_objects as go
    import streamlit as st

    st.markdown("---")
    st.subheader("👤 Detailed Player Profile")
    data = profile_data(repo, player)

    with st.container(border=True):
        photo_col, info_col = st.columns([1, 4])
        with photo_col:
            st.image(photo_url, width=156, caption=player.web_name)
        with info_col:
            header = profile_header_values(player)
            st.markdown(f"### {header['name']}")
            st.markdown(f"**{header['team']}** · {header['position']} ({header['position_code']})")
            selected = format_profile_value(header["selected_by_percent"], 1)
            form = format_profile_value(header["form"], 1)
            points = format_profile_value(header["total_points"], 0)
            st.markdown(
                f"ID: **{header['player_id']}**　 ·　 Price: **£{format_profile_value(header['price'], 1)}m**　 ·　 "
                f"Form: **{form}**　 ·　 Total points: **{points}**"
            )
            st.markdown(f"Selected: **{selected}%**")
            st.markdown(f"{header['availability_icon']} **{header['availability']}**")

    metrics = player_metric_cards(player)
    st.markdown("#### Key Statistics")
    for start in range(0, len(metrics), 4):
        row = st.columns(min(4, len(metrics) - start))
        for column, (label, value) in zip(row, metrics[start:start + 4]):
            with column:
                st.metric(label, format_profile_value(value))

    form_col, radar_col = st.columns([1, 1])
    with form_col:
        with st.container(border=True):
            st.markdown("#### 📈 Recent Form (Last 5 Gameweeks)")
            if data["history"]:
                form_frame = pd.DataFrame(data["history"])
                chart = go.Figure(go.Bar(
                    x=form_frame["Gameweek"],
                    y=form_frame["Points"],
                    marker_color="#00ff87",
                    hovertemplate="%{x}<br>Points: %{y}<extra></extra>",
                ))
                chart.update_layout(
                    height=300,
                    margin=dict(l=8, r=8, t=12, b=8),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#e8f0eb",
                    xaxis_title=None,
                    yaxis_title="FPL points",
                    yaxis=dict(gridcolor="rgba(220,240,228,0.12)", rangemode="tozero"),
                )
                st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})
                if any(row["Gameweek"].endswith(" *") for row in data["history"]):
                    st.caption("* Provisional FPL points; the source may revise them.")
            else:
                st.info("No completed gameweek history is available for this player.")

    with radar_col:
        with st.container(border=True):
            st.markdown("#### 📊 Key Statistics (Per 90 Minutes)")
            radar_metrics = per90_profile_metrics(player)
            if len(radar_metrics) >= 3:
                labels = [metric[0] for metric in radar_metrics]
                actual_values = [metric[1] for metric in radar_metrics]
                scales = [metric[2] for metric in radar_metrics]
                normalized = [min(100.0, max(0.0, value / scale * 100.0)) for value, scale in zip(actual_values, scales)]
                chart = go.Figure(go.Scatterpolar(
                    r=normalized + normalized[:1],
                    theta=labels + labels[:1],
                    customdata=actual_values + actual_values[:1],
                    fill="toself",
                    fillcolor="rgba(0,255,135,0.18)",
                    line=dict(color="#00ff87", width=2),
                    hovertemplate="%{theta}<br>Actual: %{customdata:.2f} per 90<extra></extra>",
                ))
                chart.update_layout(
                    height=300,
                    margin=dict(l=36, r=36, t=12, b=12),
                    paper_bgcolor="rgba(0,0,0,0)",
                    font_color="#e8f0eb",
                    showlegend=False,
                    polar=dict(
                        bgcolor="rgba(0,0,0,0)",
                        radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100],
                                        gridcolor="rgba(220,240,228,0.14)", tickfont=dict(size=9)),
                        angularaxis=dict(gridcolor="rgba(220,240,228,0.14)", tickfont=dict(size=9)),
                    ),
                )
                st.plotly_chart(chart, use_container_width=True, config={"displayModeBar": False})
                actual_text = " · ".join(
                    f"{label}: {value:.2f}" for label, value, _ in radar_metrics
                )
                st.caption("Axes normalized to typical per-90 ranges; values below are from this player record. " + actual_text)
            else:
                st.info("Not enough minutes or available position metrics for a per-90 radar.")

    with st.container(border=True):
        st.markdown("#### 📅 Upcoming Fixtures")
        if not data["fixtures"]:
            st.info("No upcoming fixtures are available in the fixture database.")
        else:
            for fixture in data["fixtures"]:
                left, center, right = st.columns([1, 3, 2])
                with left:
                    st.markdown(f"**{fixture['Gameweek']}**")
                with center:
                    st.markdown(f"vs {fixture['Opponent']} ({fixture['Venue'][-2]}) · {fixture['Date']}")
                with right:
                    fdr = fixture["FDR"]
                    color = FDR_COLORS.get(fdr, "#8b949e")
                    st.markdown(
                        f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
                        f'background:{escape(color)};color:#06110d;font-weight:700">FDR {escape(str(fdr))}</span>',
                        unsafe_allow_html=True,
                    )
