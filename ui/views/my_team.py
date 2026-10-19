"""Dedicated Phase 4 squad workspace built on the existing FPL data and scoring stack."""

from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from src.analytics.captain_picker import CaptainPicker
from src.analytics.scoring import DecisionScoringEngine
from src.analytics.squad_intelligence import (
    analyze_player_risk,
    build_fixture_timeline,
    build_squad_report,
    calculate_form_trend,
    evaluate_transfer_plan,
    find_alternatives,
    recommend_starting_xi,
    search_players_natural_language,
    validate_squad,
)
from src.analytics.transfer_eval import TransferAnalyzer
from src.database.models import Player, SquadMember
from src.rag.assistant_service import _source_date, _source_url
from ui.components.player_photo import resolve_player_photo_urls
from ui.components.score_card import render_score_card


def render_my_team(repo, scoring_engine: DecisionScoringEngine, transfer_analyzer: TransferAnalyzer,
                   captain_picker: CaptainPicker, vector_store, llm_provider, retriever):
    st.header("🏆 My Team")
    st.caption("Manage your squad and use the same FPL data, fixture ratings, scoring engine, and news evidence as the rest of the dashboard.")
    saved_members = repo.get_my_team()
    settings = repo.get_my_team_settings()
    saved_players = [entry["player"] for entry in saved_members]
    saved_by_id = {entry["player"].id: entry for entry in saved_members}
    all_players = repo.get_players(limit=700)
    player_by_id = {p.id: p for p in all_players}
    label_by_id = {p.id: f"{p.web_name} · {p.team_short_name or p.team_name} · {p.position} · £{p.cost_float:.1f}m" for p in all_players}
    tabs = st.tabs(["Squad Overview", "Analyze My Gameweek", "Starting XI", "Transfers", "What-If Simulator", "Captain", "Alternatives", "Risk Radar", "Search & Trends", "Fixture Timeline"])

    with tabs[0]:
        _render_squad_editor(repo, all_players, player_by_id, label_by_id, saved_members, settings)
        if saved_players:
            _render_squad_summary(saved_members, settings, repo, scoring_engine)

    with tabs[1]:
        _render_gameweek_analysis(repo, saved_players, settings, scoring_engine, captain_picker, vector_store, llm_provider, player_by_id)

    with tabs[2]:
        _render_starting_xi(repo, saved_players, scoring_engine, saved_members)

    with tabs[3]:
        _render_transfer_planner(repo, saved_players, settings, player_by_id, label_by_id, scoring_engine, transfer_analyzer)

    with tabs[4]:
        _render_what_if(repo, saved_players, settings, player_by_id, label_by_id, scoring_engine, transfer_analyzer)

    with tabs[5]:
        _render_captain(saved_players, scoring_engine, captain_picker)

    with tabs[6]:
        _render_alternatives(repo, saved_players, player_by_id, label_by_id, scoring_engine, settings)

    with tabs[7]:
        _render_risk_radar(repo, saved_players, scoring_engine, vector_store)

    with tabs[8]:
        _render_player_search(repo, all_players, player_by_id, label_by_id, scoring_engine, retriever)

    with tabs[9]:
        _render_fixture_timeline(repo, saved_players, all_players, label_by_id)


def _render_squad_editor(repo, players, player_by_id, labels, saved_members, settings):
    st.subheader("Manage your 15-player squad")
    saved_ids = [entry["player"].id for entry in saved_members]
    default_captain = next((entry["player"].id for entry in saved_members if entry["is_captain"]), saved_ids[0] if saved_ids else None)
    default_vice = next((entry["player"].id for entry in saved_members if entry["is_vice_captain"]), saved_ids[1] if len(saved_ids) > 1 else None)
    with st.form("my_team_editor"):
        selected_ids = st.multiselect("Select players", options=list(player_by_id), default=saved_ids,
                                      format_func=lambda pid: labels.get(pid, str(pid)), max_selections=15,
                                      help="A valid squad has 2 GKP, 5 DEF, 5 MID, and 3 FWD, with no more than 3 players from one club.")
        selected = [player_by_id[pid] for pid in selected_ids if pid in player_by_id]
        selected_id_set = set(selected_ids)
        cap_options = [None] + selected_ids
        captain_id = st.selectbox("Captain", cap_options, index=cap_options.index(default_captain) if default_captain in cap_options else 0,
                                  format_func=lambda pid: "Select captain" if pid is None else labels.get(pid, str(pid)))
        vice_options = [None] + [pid for pid in selected_ids if pid != captain_id]
        vice_default = default_vice if default_vice in vice_options else (vice_options[1] if len(vice_options) > 1 else None)
        vice_id = st.selectbox("Vice-captain", vice_options, index=vice_options.index(vice_default) if vice_default in vice_options else 0,
                               format_func=lambda pid: "Select vice-captain" if pid is None else labels.get(pid, str(pid)))
        bank = st.number_input("Available bank (£m)", min_value=0.0, max_value=50.0, value=float(settings["bank_millions"]), step=0.1)
        free_transfers = st.number_input("Free transfers", min_value=1, max_value=5, value=int(settings["free_transfers"]), step=1)
        old_outfield_bench = [entry["player"].id for entry in sorted(saved_members, key=lambda row: row["bench_order"]) if entry["player"].position != "GKP"][-3:]
        outfield_ids = [pid for pid in selected_ids if player_by_id[pid].position != "GKP"]
        bench_ids = st.multiselect("Preferred outfield bench order (3 players)", options=outfield_ids,
                                   default=[pid for pid in old_outfield_bench if pid in selected_id_set and player_by_id[pid].position != "GKP"],
                                   format_func=lambda pid: labels.get(pid, str(pid)), max_selections=3)
        prices = pd.DataFrame([
            {"ID": p.id, "Player": p.web_name, "Purchase price (£m)": float(saved_by_price(saved_members, p.id, p.cost_float)), "Current price (£m)": p.cost_float}
            for p in selected
        ], columns=["ID", "Player", "Purchase price (£m)", "Current price (£m)"])
        st.caption("Purchase prices are editable; current prices come from the synchronized FPL data.")
        edited_prices = st.data_editor(prices, hide_index=True, use_container_width=True, disabled=["ID", "Player", "Current price (£m)"],
                                       column_config={"Purchase price (£m)": st.column_config.NumberColumn(min_value=0.0, max_value=30.0, step=0.1)})
        submitted = st.form_submit_button("Save My Team", type="primary", use_container_width=True)
    if submitted:
        valid, issues = validate_squad(selected)
        if captain_id == vice_id:
            valid = False
            issues.append("Captain and vice-captain must be different players.")
        if not valid:
            for issue in issues:
                st.error(issue)
        else:
            purchase_prices = {int(row["ID"]): float(row["Purchase price (£m)"]) for _, row in edited_prices.iterrows()}
            bench_rank = {pid: rank for rank, pid in enumerate(bench_ids, start=1)}
            members = []
            for idx, player in enumerate(selected):
                order = bench_rank.get(player.id, 10 + idx)
                members.append(SquadMember(player.id, purchase_prices.get(player.id, player.cost_float), order,
                                           player.id == captain_id, player.id == vice_id))
            repo.save_my_team(members, bank, free_transfers)
            st.session_state.pop("phase4_report", None)
            st.success("Your squad and settings were saved to the existing SQLite database.")
            st.rerun()


def saved_by_price(saved_members, player_id, current_price):
    return next((float(item["purchase_price"]) for item in saved_members if item["player"].id == player_id), current_price)


def _render_squad_summary(members, settings, repo, scoring):
    players = [item["player"] for item in members]
    current_value = sum(p.cost_float for p in players)
    cols = st.columns(4)
    cols[0].metric("Players", f"{len(players)} / 15")
    cols[1].metric("Bank", f"£{settings['bank_millions']:.1f}m")
    cols[2].metric("Free transfers", settings["free_transfers"])
    cols[3].metric("Squad current value", f"£{current_value:.1f}m")
    available = sum(p.status == "a" for p in players)
    doubtful = sum(p.status == "d" for p in players)
    injured = sum(p.status in {"i", "s", "u"} for p in players)
    rotation = sum(p.minutes > 0 and p.total_points > 0 and p.minutes / p.total_points < 30 for p in players)
    st.markdown("### Squad Health")
    cols = st.columns(4)
    cols[0].metric("✅ Available", available)
    cols[1].metric("⚠ Doubtful", doubtful)
    cols[2].metric("❌ Injured / unavailable", injured)
    cols[3].metric("🔄 Possible minutes risk", rotation)
    valid, _ = validate_squad(players)
    if valid:
        xi = recommend_starting_xi(players, repo, scoring)
        st.markdown(f"### Starting XI · {xi.formation}")
        _render_cards(xi.starting, repo)
        st.markdown("### Bench")
        _render_cards(xi.bench, repo)
    else:
        st.warning("Save a valid 15-player squad to calculate the starting XI.")


def _render_cards(players, repo):
    if not players:
        return
    urls = resolve_player_photo_urls(players, repo)
    cols = st.columns(min(5, len(players)))
    for index, player in enumerate(players):
        with cols[index % len(cols)]:
            st.image(urls[player.id], width=64)
            st.markdown(f"**{player.web_name}**  \n{player.team_short_name or player.team_name} · {player.position}  \n£{player.cost_float:.1f}m · Form {player.form} · {player.total_points} pts")
            st.caption(_availability(player))


def _render_starting_xi(repo, players, scoring, members):
    st.subheader("Recommended Starting XI")
    valid, issues = validate_squad(players)
    if not valid:
        st.info("Save a valid 15-player squad to see the optimized XI.")
        return
    result = recommend_starting_xi(players, repo, scoring, manual_bench_order=[item["player"].id for item in sorted(members, key=lambda item: item["bench_order"])])
    st.markdown(f"**Formation: {result.formation}** · Starting XI score total {sum(result.scores[p.id] for p in result.starting):.1f}")
    _render_position_group("Goalkeeper", [p for p in result.starting if p.position == "GKP"], result)
    _render_position_group("Defenders", [p for p in result.starting if p.position == "DEF"], result)
    _render_position_group("Midfielders", [p for p in result.starting if p.position == "MID"], result)
    _render_position_group("Forwards", [p for p in result.starting if p.position == "FWD"], result)
    st.markdown("### Bench (goalkeeper first, then selected outfield order)")
    for rank, player in enumerate(result.bench, 1):
        st.markdown(f"{rank}. **{player.web_name}** · {player.position} · Score {result.scores[player.id]:.1f}/100 · {_availability(player)}")
        position_starters = [p for p in result.starting if p.position == player.position]
        benched_for_selection = player.position != "GKP" and position_starters and result.utilities[player.id] < min(result.utilities[p.id] for p in position_starters)
        if _is_unavailable(player) or benched_for_selection:
            st.caption("Benched because the position-specific XI score/status ranking favored the available starters.")


def _render_position_group(title, players, result):
    st.markdown(f"**{title}**")
    for player in players:
        st.markdown(f"- **{player.web_name}** · {player.team_short_name or player.team_name} · Score {result.scores[player.id]:.1f}/100 · {_availability(player)}")


def _render_gameweek_analysis(repo, players, settings, scoring, captain_picker, vector_store, llm_provider, player_by_id):
    st.subheader("Analyze My Gameweek")
    valid, issues = validate_squad(players)
    if not valid:
        st.info("Save a valid 15-player squad first. This report will combine your stats, fixtures, risks, RAG evidence, XI, captain, and transfer options.")
        return
    if st.button("⭐ Analyze My Gameweek", type="primary", key="analyze_my_gameweek"):
        with st.spinner("Analyzing squad form, fixtures, risk, and relevant indexed news…"):
            report = build_squad_report(players, repo, scoring, captain_picker, vector_store, llm_provider, bank_millions=settings["bank_millions"])
        st.session_state["phase4_report"] = report
    report = st.session_state.get("phase4_report")
    if not report:
        st.info("Choose Analyze My Gameweek to generate the squad-aware report.")
        return
    st.markdown("## GAMEWEEK REPORT")
    cols = st.columns(4)
    cols[0].metric("Squad health", f"{report.health_score}/100")
    cols[1].metric("Injured / unavailable", report.injury_count)
    cols[2].metric("Doubtful", report.doubtful_count)
    cols[3].metric("Rotation concerns", report.rotation_count)
    st.markdown(f"**⭐ Captain:** {report.captains[0].player.web_name if report.captains else 'Not available'}")
    st.markdown(f"**🥈 Vice-captain:** {report.captains[1].player.web_name if len(report.captains) > 1 else 'Not available'}")
    st.markdown(f"**📋 Recommended formation:** {report.xi.formation}")
    st.markdown("### Recommended XI")
    _render_position_group("Starting XI", report.xi.starting, report.xi)
    st.markdown("**Bench:** " + " · ".join(p.web_name for p in report.xi.bench))
    st.markdown("### Squad risks")
    st.markdown(f"Doubtful: {report.doubtful_count} · Injured/unavailable: {report.injury_count} · Rotation concerns: {report.rotation_count} · Difficult fixture risks: {report.difficult_fixture_count}")
    st.markdown("### Transfer opportunities and alternatives")
    for player in report.transfer_candidates:
        score = report.xi.scores.get(player.id, 0)
        st.markdown(f"**Consider reviewing {player.web_name}** · deterministic score {score:.1f}/100 · {_availability(player)}")
        alts = report.alternatives.get(player.id, [])
        if alts:
            st.caption("Alternatives: " + " · ".join(f"{target.web_name} ({value:.1f})" for target, value in alts))
    st.markdown("### Form highlights")
    for trend in sorted(report.trends, key=lambda item: (item.average_points is not None, item.average_points or -1), reverse=True)[:5]:
        avg = f"{trend.average_points:.2f}" if trend.average_points is not None else "Not available"
        st.markdown(f"{trend.player.web_name}: last 5 GW average {avg} pts, trend {trend.direction}.")
    st.markdown("### Injury and news evidence")
    if report.injury_profiles:
        for player_id, profile in report.injury_profiles.items():
            player = next((p for p in players if p.id == player_id), None)
            reconciliation = profile["reconciliation"]
            info = reconciliation.get("info", {})
            source = reconciliation.get("source", {})
            metadata = source.get("metadata", {})
            st.markdown(f"**{player.web_name if player else player_id}:** {_availability(player) if player else 'Status unavailable'} · {info.get('injury_problem') or 'Problem not available'} · Expected return: {info.get('expected_return_date') or 'Not publicly available'} · Expected Matchweek: {('MW' + str(info.get('matchweek'))) if info.get('matchweek') else 'Not available'}")
            if source:
                url = source.get("url") or metadata.get("link") or metadata.get("url")
                source_label = f"{source.get('source') or metadata.get('source') or 'Source unavailable'} · {_source_date(metadata) or 'Date unavailable'}"
                st.markdown(f"Source: [{source_label}]({url})" if url and str(url).startswith(("http://", "https://")) else f"Source: {source_label} · URL not available")
    _render_evidence(report.evidence)
    st.markdown("### Gemini / offline explanation")
    st.write(report.explanation or "No explanation was generated.")


def _render_evidence(evidence):
    if not evidence:
        st.info("No relevant recent squad news evidence was found.")
        return
    for idx, doc in enumerate(evidence[:10], 1):
        metadata = doc.get("metadata", {})
        source = metadata.get("source") or "Unknown source"
        published = _source_date(metadata) or "Date unavailable"
        title = metadata.get("title") or source
        url = _source_url(doc)
        st.markdown(f"[{title} — {source} · {published}]({url})" if url else f"**{title} — {source} · {published}**")
        with st.expander(f"Evidence {idx}"):
            st.write(doc.get("text", ""))


def _render_transfer_planner(repo, squad, settings, player_by_id, labels, scoring, transfer_analyzer):
    st.subheader("Transfer Planner")
    if not squad:
        st.info("Save your squad first.")
        return
    squad_ids = [p.id for p in squad]
    player_ids = list(player_by_id)
    with st.form("single_transfer_plan"):
        out_id = st.selectbox("Player OUT", squad_ids, format_func=lambda pid: labels.get(pid, str(pid)))
        outgoing = player_by_id[out_id]
        incoming_ids = [pid for pid in player_ids if player_by_id[pid].position == outgoing.position and pid not in squad_ids]
        in_id = st.selectbox("Player IN (same position)", incoming_ids, format_func=lambda pid: labels.get(pid, str(pid))) if incoming_ids else None
        analyze_single = st.form_submit_button("Analyze Transfer", use_container_width=True)
    if analyze_single and in_id:
        plan = evaluate_transfer_plan(squad, [(outgoing, player_by_id[in_id])], settings["bank_millions"], settings["free_transfers"], repo, scoring, transfer_analyzer)
        st.session_state["single_transfer_plan"] = plan
    plan = st.session_state.get("single_transfer_plan")
    if plan:
        _render_transfer_plan(plan)
        if plan.moves:
            _render_alternative_list(find_alternatives(plan.moves[0][0], squad, repo, scoring, settings["bank_millions"], 5), repo)
        if plan.legal and plan.affordable and st.button("Apply Transfer to My Team", type="primary", key="apply_single_transfer"):
            _apply_transfer(repo, squad, settings, plan)

    st.markdown("### Multi-Transfer Planner")
    with st.form("multi_transfer_plan"):
        outs = st.multiselect("Players OUT", squad_ids, format_func=lambda pid: labels.get(pid, str(pid)), max_selections=4, key="multi_outs")
        ins = st.multiselect("Players IN (choose matching positions in the same order)", player_ids, format_func=lambda pid: labels.get(pid, str(pid)), max_selections=4, key="multi_ins")
        analyze_multi = st.form_submit_button("Analyze Combined Transfers", use_container_width=True)
    if analyze_multi:
        if len(outs) != len(ins) or not outs:
            st.error("Select the same positive number of players OUT and IN.")
        else:
            moves = [(player_by_id[o], player_by_id[i]) for o, i in zip(outs, ins)]
            plan = evaluate_transfer_plan(squad, moves, settings["bank_millions"], settings["free_transfers"], repo, scoring, transfer_analyzer)
            st.session_state["multi_transfer_plan"] = plan
    multi_plan = st.session_state.get("multi_transfer_plan")
    if multi_plan:
        _render_transfer_plan(multi_plan)
        if multi_plan.legal and multi_plan.affordable and st.button("Apply Combined Transfers to My Team", type="primary", key="apply_multi_transfer"):
            _apply_transfer(repo, squad, settings, multi_plan)


def _render_transfer_plan(plan):
    st.markdown("### Transfer analysis")
    for number, (outgoing, incoming) in enumerate(plan.moves, 1):
        st.markdown(f"**Transfer {number}:** {outgoing.web_name} ({outgoing.position}) → {incoming.web_name} ({incoming.position})")
    cols = st.columns(5)
    cols[0].metric("Budget impact", f"£{plan.budget_impact:+.1f}m")
    cols[1].metric("Bank after", f"£{plan.bank_after:.1f}m")
    cols[2].metric("Affordable", "Yes" if plan.affordable else "No")
    cols[3].metric("Squad legal", "Yes" if plan.legal else "No")
    cols[4].metric("Score delta", f"{plan.score_delta:+.1f}")
    st.markdown(f"**Verdict:** {plan.verdict} · fixture swing (lower is easier): {plan.fixture_delta:+.2f} FDR · risk improvement: {plan.risk_delta:+d}")
    for issue in plan.legality_issues:
        st.warning(issue)
    for item in plan.simulations:
        st.caption(f"{item['out'].web_name}: {item['score_out'].final_score:.1f}/100 → {item['in'].web_name}: {item['score_in'].final_score:.1f}/100; score change {item['decision_delta']:+.1f}")


def _apply_transfer(repo, squad, settings, plan):
    replacements = {out.id: incoming for out, incoming in plan.moves}
    saved_members = repo.get_my_team()
    members = []
    for idx, player in enumerate(squad):
        incoming = replacements.get(player.id)
        chosen = incoming or player
        members.append(SquadMember(chosen.id, chosen.cost_float if incoming else saved_by_price(saved_members, player.id, player.cost_float),
                                   idx, False if incoming else _saved_flag(saved_members, player.id, "is_captain"),
                                   False if incoming else _saved_flag(saved_members, player.id, "is_vice_captain")))
    count = len(plan.moves)
    next_free = max(1, int(settings["free_transfers"]) - max(0, count - 1))
    repo.save_my_team(members, plan.bank_after, next_free)
    st.session_state.pop("single_transfer_plan", None)
    st.session_state.pop("multi_transfer_plan", None)
    st.session_state.pop("phase4_report", None)
    st.success("Transfer applied to the saved team.")
    st.rerun()


def _saved_flag(members, player_id, key):
    return next((bool(item[key]) for item in members if item["player"].id == player_id), False)


def _render_what_if(repo, squad, settings, player_by_id, labels, scoring, transfer_analyzer):
    st.subheader("What-If Simulator")
    if not squad:
        st.info("Save your squad first. Simulations never write to the saved team.")
        return
    squad_ids = [p.id for p in squad]
    with st.form("what_if_simulation"):
        out_id = st.selectbox("Simulate Player OUT", squad_ids, format_func=lambda pid: labels.get(pid, str(pid)), key="what_if_out")
        outgoing = player_by_id[out_id]
        targets = [pid for pid, player in player_by_id.items() if player.position == outgoing.position and pid not in squad_ids]
        in_id = st.selectbox("Simulate Player IN", targets, format_func=lambda pid: labels.get(pid, str(pid)), key="what_if_in") if targets else None
        simulate = st.form_submit_button("Simulate Transfer")
    if simulate and in_id:
        result = evaluate_transfer_plan(squad, [(outgoing, player_by_id[in_id])], settings["bank_millions"], settings["free_transfers"], repo, scoring, transfer_analyzer)
        st.session_state["what_if_plan"] = result
    result = st.session_state.get("what_if_plan")
    if result:
        st.markdown("**Current Team → Simulated Team**")
        _render_transfer_plan(result)
        st.caption("Simulation only: your saved squad remains unchanged until a transfer is applied from the Transfer Planner.")
    if st.button("Reset Simulation", key="reset_what_if"):
        st.session_state.pop("what_if_plan", None)
        st.rerun()


def _render_captain(players, scoring, picker):
    st.subheader("Captain / Vice-Captain")
    if not players:
        st.info("Save your squad first.")
        return
    picks = picker.get_top_captains(top_n=2, candidate_players=list(players))
    if len(picks) < 2:
        st.info("The current squad does not have two eligible captain candidates.")
        return
    for label, pick in zip(("⭐ CAPTAIN", "🥈 VICE-CAPTAIN"), picks):
        st.markdown(f"### {label}: {pick.player.web_name}")
        st.metric("Deterministic captain confidence", f"{pick.captain_index}/100")
        st.caption(pick.rationale)
        st.markdown(f"Why: Form {pick.player.form}; next opponent {pick.next_opponent} ({'Home' if pick.is_home else 'Away'}); FDR {pick.fdr}; {_availability(pick.player)}.")


def _render_alternatives(repo, squad, player_by_id, labels, scoring, settings):
    st.subheader("Alternative Transfer Options")
    if not squad:
        st.info("Save your squad first.")
        return
    out_id = st.selectbox("Find alternatives for", [p.id for p in squad], format_func=lambda pid: labels.get(pid, str(pid)), key="alternative_out")
    outgoing = player_by_id[out_id]
    alts = find_alternatives(outgoing, squad, repo, scoring, settings["bank_millions"], limit=8)
    st.markdown(f"**Transfer target role:** {outgoing.position} · maximum price £{outgoing.cost_float + settings['bank_millions']:.1f}m · sorted by existing score, form, points")
    _render_alternative_list(alts, repo)


def _render_alternative_list(alternatives, repo):
    if not alternatives:
        st.info("No eligible alternatives match the position, budget, squad, fixture, and availability filters.")
        return
    _render_cards([player for player, _ in alternatives], repo)
    for rank, (player, score) in enumerate(alternatives, 1):
        st.markdown(f"{rank}. **{player.web_name}** · {player.team_short_name or player.team_name} · £{player.cost_float:.1f}m · Form {player.form} · Decision Confidence {score:.1f}/100 · {_availability(player)}")


def _render_risk_radar(repo, players, scoring, vector_store):
    st.subheader("Player Risk Radar")
    if not players:
        st.info("Save your squad first.")
        return
    high_ids = {p.id for p in players if p.status in {"i", "d", "s", "u"}}
    docs_by_id = defaultdict(list)
    for player in players:
        if player.id in high_ids:
            try:
                docs_by_id[player.id] = vector_store.query(f"{player.web_name} {player.team_name or ''} injury availability fitness", n_results=2)
            except Exception:
                docs_by_id[player.id] = []
    for player in players:
        fixtures = repo.get_upcoming_fixtures(team_id=player.team_id, limit=5)
        history = repo.get_player_gameweek_history(player.id, limit=5)
        risk = analyze_player_risk(player, fixtures, history, docs_by_id[player.id])
        st.markdown(f"**{player.web_name} · Overall {risk.overall}**")
        st.write(" · ".join(f"{category}: {level}" for category, level in risk.levels.items()))
        with st.expander(f"Why {player.web_name}?"):
            for category, reason in risk.reasons.items():
                st.markdown(f"- **{category} — {risk.levels[category]}:** {reason}")
            _render_evidence(docs_by_id[player.id])


def _render_player_search(repo, players, player_by_id, labels, scoring, retriever):
    st.subheader("Natural-Language Player Search")
    query = st.text_input("Describe the players you want", placeholder="Find midfielders under £8m with good fixtures and no major injury concerns.", key="phase4_player_search")
    if not query:
        return
    results = search_players_natural_language(query, repo, scoring, limit=30)
    st.caption(f"{len(results)} matching players · filters are applied from SQLite FPL fields, stored fixtures/FDR, and gameweek history.")
    _render_cards([item["player"] for item in results[:12]], repo)
    for item in results:
        p = item["player"]
        st.markdown(f"**{p.web_name}** · {p.team_short_name or p.team_name} · {p.position} · £{p.cost_float:.1f}m · Form {p.form} · {p.total_points} pts · Avg FDR {item['avg_fdr']:.2f} · Score {item['score']:.1f}/100 · {_availability(p)}")
        trend = item["trend"]
        if trend.rounds:
            st.caption(f"Recent: {', '.join(f'GW{gw} {pts} pts/{mins}m' for gw, pts, mins in zip(trend.rounds, trend.points, trend.minutes))} · avg {trend.average_points:.2f} · trend {trend.direction}")
    if any(term in query.casefold() for term in ("news", "injury", "manager", "availability update")):
        st.markdown("**Relevant indexed evidence**")
        for item in results[:5]:
            context = retriever.retrieve(f"{item['player'].web_name} {item['player'].team_name or ''} {query}", top_k_news=2)
            _render_evidence(context.news_chunks)


def _render_fixture_timeline(repo, squad, all_players, labels):
    st.subheader("Fixture Timeline")
    if not squad:
        st.info("Save your squad to view your aggregate fixtures.")
        return
    mode = st.radio("Timeline view", ["My Team", "Player", "Club"], horizontal=True)
    teams = repo.get_all_teams()
    if mode == "Player":
        player_id = st.selectbox("Player", [p.id for p in squad], format_func=lambda pid: labels.get(pid, str(pid)))
        player = next(p for p in squad if p.id == player_id)
        timelines = [(player.web_name, build_fixture_timeline(player, repo.get_upcoming_fixtures(player.team_id, 5), teams, 5))]
    elif mode == "Club":
        clubs = {p.team_id: p.team_name for p in all_players}
        team_id = st.selectbox("Club", sorted(clubs), format_func=lambda tid: clubs[tid])
        club_player = next(p for p in all_players if p.team_id == team_id)
        timelines = [(clubs[team_id], build_fixture_timeline(club_player, repo.get_upcoming_fixtures(team_id, 5), teams, 5))]
    else:
        aggregate = defaultdict(list)
        for player in squad:
            fixtures = repo.get_upcoming_fixtures(team_id=player.team_id, limit=5)
            for entry in build_fixture_timeline(player, fixtures, teams, 5):
                aggregate[entry["gameweek"]].append(entry["fdr"])
        st.markdown("**Average FDR across your squad (lower is easier)**")
        st.dataframe(pd.DataFrame([{"Gameweek": gw, "Squad average FDR": round(sum(values) / len(values), 2), "Players with fixture": len(values)} for gw, values in sorted(aggregate.items())]), hide_index=True, use_container_width=True)
        return
    for title, timeline in timelines:
        st.markdown(f"**{title}**")
        st.dataframe(pd.DataFrame([{"GW": item["gameweek"], "Opponent": item["opponent"], "Venue": "H" if item["home"] else "A", "FDR": item["fdr"], "Kickoff": item["kickoff"]} for item in timeline]), hide_index=True, use_container_width=True)


def _availability(player):
    labels = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable"}
    chance = player.chance_of_playing_next_round
    return labels.get(player.status, player.status) + (f" ({chance}% chance)" if chance is not None else "")


def _is_unavailable(player):
    return player.status in {"i", "s", "u"} or player.chance_of_playing_next_round == 0
