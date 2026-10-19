"""Phase 3 grounded conversational assistant view."""

import streamlit as st

from src.analytics.captain_picker import CaptainPicker
from src.analytics.transfer_eval import TransferAnalyzer
from src.rag.assistant_service import FPLAssistant
from ui.components.player_photo import resolve_player_photo_url
from ui.components.score_card import render_score_card


def render_rag_assistant(retriever, llm_provider, scoring_engine, transfer_analyzer=None, captain_picker=None):
    st.header("🤖 FPL AI Assistant")
    st.caption("Grounded in current FPL data, fixture records, deterministic scoring, and indexed news evidence.")
    transfer_analyzer = transfer_analyzer or TransferAnalyzer(scoring_engine=scoring_engine, repository=retriever.repo)
    captain_picker = captain_picker or CaptainPicker(repository=retriever.repo, scoring_engine=scoring_engine)
    assistant = FPLAssistant(retriever, llm_provider, scoring_engine, transfer_analyzer, captain_picker)

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [{"role": "assistant", "content": "👋 Ask me about transfers, captaincy, player comparisons, fixtures, or source-backed injury updates."}]
    if "last_transfer_out" not in st.session_state:
        st.session_state.last_transfer_out = None
    st.markdown("**⚡ Quick questions**")
    suggestion_cols = st.columns(4)
    prompt_to_submit = None
    suggestions = [
        "Who should I captain this gameweek?",
        "What is the latest injury situation for Havertz?",
        "Should I transfer Haaland out for Gakpo?",
        "Who is a better option for the next 3 gameweeks, Saka or Gakpo?",
    ]
    for col, prompt in zip(suggestion_cols, suggestions):
        with col:
            if st.button(prompt, use_container_width=True):
                prompt_to_submit = prompt
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    user_question = st.chat_input("Ask an FPL question…")
    question = prompt_to_submit or user_question
    if not question:
        return
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Combining FPL data, fixtures, and relevant news evidence…"):
            result = assistant.analyze(question, prior_transfer_out=st.session_state.last_transfer_out)
        if result.transfer:
            st.session_state.last_transfer_out = result.transfer.player_out.web_name
        st.caption(f"Reasoning provider: {result.provider_name}" + (" · Offline/mock mode" if result.fallback else ""))
        if result.parsed.ambiguous:
            st.warning("I found an ambiguous player name. Please clarify: " + ", ".join(result.parsed.ambiguous))
        if result.parsed.intent == "transfer_analysis":
            if result.transfer:
                t = result.transfer
                st.subheader(f"Transfer Analysis · {t.player_out.web_name} → {t.player_in.web_name}")
                cols = st.columns(4)
                cols[0].metric("Cost change", f"£{t.cost_diff:+.1f}m")
                cols[1].metric(f"{t.player_out.web_name} score", f"{t.score_out.final_score}/100")
                cols[2].metric(f"{t.player_in.web_name} score", f"{t.score_in.final_score}/100")
                cols[3].metric("Score change", f"{t.confidence_delta:+.1f}")
                st.markdown(f"**Engine verdict:** {t.verdict}")
                st.caption("Affordability is evaluated with £0.0m bank because no bank balance was supplied.")
            else:
                st.info("Name both the player to sell and the replacement, for example: ‘Should I transfer Haaland out for Gakpo?’")
        if result.captains:
            st.subheader("Captain candidates · ranked by the existing captain engine")
            for item in result.captains:
                st.markdown(f"**{item.rank}. {item.player.web_name} — {item.captain_index}/100** · vs {item.next_opponent} ({'Home' if item.is_home else 'Away'}, FDR {item.fdr}) · Form {item.player.form} · {_availability(item.player)}")
        if result.injury:
            injury = result.injury
            player = injury["player"]
            col_photo, col_info = st.columns([1, 5])
            with col_photo:
                st.image(resolve_player_photo_url(player, retriever.repo), width=84)
            with col_info:
                st.markdown(f"### {player.full_name}\n**{player.team_name or 'Club unavailable'}**")
                st.markdown(f"**Current availability:** {injury['availability']}")
                rec = injury.get("reconciliation")
                info = rec.get("info", {}) if rec else {}
                source = rec.get("source", {}) if rec else {}
                if rec and rec.get("conflict"):
                    st.warning("Sources disagree on the expected return date. The selected source is shown with supporting evidence below.")
                if rec and rec.get("stale"):
                    st.warning("The selected return-date source is stale; treat this information cautiously.")
                st.markdown(f"**Injury:** {info.get('injury_problem') or 'Not available'}  \n**Injury started:** {info.get('injury_start_date') or 'Not available'}  \n**Expected return:** {info.get('expected_return_date') or 'Not publicly available'}  \n**Recovery:** {info.get('recovery_duration') or 'Not available'}  \n**Expected Matchweek:** {('Estimated ' if info.get('estimated') else '') + 'MW' + str(info.get('matchweek')) if info.get('matchweek') else 'Not available'}")
                if source:
                    m = source.get("metadata", {})
                    st.caption(f"Source: {m.get('source') or 'Unknown'} · Updated: {_source_date(m) or 'Date unavailable'} · Last verified: {rec.get('last_verified_at') or 'Not available'} · Return confidence: {info.get('return_date_confidence', 'Unknown')}")
                    if info.get("expected_return_date"):
                        url = m.get("url") or m.get("link") or m.get("source_url")
                        if url and str(url).startswith(("https://", "http://")):
                            st.markdown(f"[View return-date source]({url})")
                        else:
                            st.caption("Source URL: Not available in the indexed record")
                else:
                    st.caption("No linked trusted injury source with a return date was found.")
        for ctx in result.player_contexts:
            player = ctx.target_player
            if not player:
                continue
            with st.expander(f"{player.web_name} · FPL data and next fixtures", expanded=len(result.player_contexts) == 1):
                cols = st.columns(5)
                cols[0].metric("Price", f"£{player.cost_float:.1f}m")
                cols[1].metric("Form", f"{player.form}")
                cols[2].metric("Points", str(player.total_points))
                cols[3].metric("PPG", str(player.points_per_game))
                cols[4].metric("Minutes", str(player.minutes))
                st.markdown(f"{player.position} · {_availability(player)} · Goals {player.goals_scored} · Assists {player.assists} · xG {player.expected_goals} · xA {player.expected_assists} · ICT {player.ict_index} · Ownership {player.selected_by_percent}%")
                if ctx.upcoming_fixtures:
                    st.markdown("**Upcoming fixtures**")
                    for fx in ctx.upcoming_fixtures[:result.parsed.horizon]:
                        home = fx.team_h == player.team_id
                        opp = fx.team_a_name if home else fx.team_h_name
                        fdr = fx.team_h_difficulty if home else fx.team_a_difficulty
                        st.markdown(f"GW{fx.event} · {opp or 'Opponent unavailable'} · {'Home' if home else 'Away'} · FDR {fdr} · {fx.kickoff_time or 'Date unavailable'}")
                score = result.scores.get(player.id)
                if score:
                    render_score_card(score, player_name=player.web_name)
        st.subheader("AI explanation")
        st.markdown(result.answer)
        st.subheader("Evidence")
        if result.evidence:
            for idx, item in enumerate(result.evidence[:8], 1):
                meta = item.get("metadata", {})
                title = meta.get("title") or meta.get("source") or f"Evidence {idx}"
                source, pub = meta.get("source") or "Source unavailable", _source_date(meta)
                url = meta.get("url") or meta.get("link") or meta.get("source_url")
                label = f"{title} — {source} · {pub or 'date unavailable'}"
                st.markdown(f"[{label}]({url})" if url and str(url).startswith(("https://", "http://")) else label)
                with st.expander("Show evidence text", expanded=False):
                    st.write(item.get("text", ""))
        else:
            st.info("No relevant recent news or injury evidence was found.")
    st.session_state.chat_history.append({"role": "assistant", "content": result.answer})


def _availability(player):
    labels = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable"}
    chance = player.chance_of_playing_next_round
    return labels.get(player.status, player.status) + (f" ({chance}% chance)" if chance is not None else "")


def _source_date(metadata):
    return metadata.get("published_at") or metadata.get("updated_at") or metadata.get("timestamp") or ""
