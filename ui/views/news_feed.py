"""
View 5: Live Football News, Press Conferences & Injury Bulletins Radar.
Integrates semantic news retrieval from ChromaDB with structured player intelligence from SQLite.
Safe, sanitized UI rendering with zero exposed raw HTML tags.
"""

import html
import re
from typing import Optional, Dict, Any, List
import streamlit as st
from config.constants import STATUS_MAP, FDR_LABELS
from src.rag.vector_store import ChromaVectorStore
from src.database.repository import FPLRepository
from src.rag.retriever import HybridRetriever
from src.ingestion.sync_news import NewsIngestionPipeline
from src.rag.injury_intelligence import reconcile_injury_sources
from ui.components.player_photo import resolve_player_photo_url


def sanitize_text(val: Any) -> str:
    """
    Safely strip HTML tags, decode HTML entities, and normalize whitespace
    to guarantee no raw markup or entities leak into the UI.
    """
    if not val:
        return ""
    text = str(val)
    # Decode HTML entities (e.g. &amp;, &quot;, &#39;, &lt;, &gt;)
    text = html.unescape(text)
    # Strip HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Normalize multiple whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def injury_severity_key(status: str) -> str:
    """Map the current FPL status code to the News Radar severity filter values."""
    return {"i": "out", "d": "doubtful", "s": "suspended", "a": "fit"}.get(status, "unknown")


def render_news_feed(
    vector_store: ChromaVectorStore,
    repo: Optional[FPLRepository] = None,
    retriever: Optional[HybridRetriever] = None,
):
    """Render the Hybrid News & Injury Radar view with sanitized readable cards."""
    repo = repo or FPLRepository()
    retriever = retriever or HybridRetriever(vector_store=vector_store, repository=repo)

    st.header("📰 Live Football News & Injury Radar")
    st.caption("Fresh intelligence retrieved from multi-source RSS feeds and Official Premier League Medical Bulletins.")

    # 1. Top Metrics Banner
    all_players = repo.get_players(limit=750)
    players_by_tag: Dict[str, List[Any]] = {}
    for known_player in all_players:
        for name in (known_player.web_name, known_player.full_name):
            if name:
                matches = players_by_tag.setdefault(name.strip().casefold(), [])
                if all(existing.id != known_player.id for existing in matches):
                    matches.append(known_player)
    injured_count = len([p for p in all_players if p.status == "i"])
    doubtful_count = len([p for p in all_players if p.status == "d"])
    total_vectors = vector_store.count()

    col_m1, col_m2, col_m3, col_m4 = st.columns([1, 1, 1, 1.3])
    col_m1.metric("Indexed News Chunks", total_vectors)
    col_m2.metric("Confirmed Injured", injured_count)
    col_m3.metric("Flagged Doubtful", doubtful_count)

    with col_m4:
        st.write("")
        if st.button("🔄 Sync News & Bulletins", use_container_width=True):
            with st.spinner("Fetching RSS feeds, official bulletins, and generating embeddings..."):
                pipeline = NewsIngestionPipeline(vector_store=vector_store, repository=repo)
                metrics = pipeline.run(max_per_feed=25)
                st.success(
                    f"Sync complete! Processed {metrics['indexed_chunks']} chunks ({metrics['injury_bulletins']} official bulletins, {metrics['rss_articles']} RSS articles)."
                )
                st.rerun()

    st.markdown("---")

    # 2. Search & Filter Controls
    col_search, col_cat, col_sev = st.columns([3, 1.5, 1.5])
    with col_search:
        search_query = st.text_input(
            "🔍 Semantic Search (Player name, injury, club, or tactical keyword):",
            placeholder="e.g. Bukayo Saka, hamstring strain, Haaland, knee injury, Palmer...",
        )
    with col_cat:
        category_options = ["All Categories", "Injury", "Press Conference", "Transfer", "Tactics"]
        selected_cat = st.selectbox("Category Filter", category_options, index=0)
    with col_sev:
        severity_options = ["All Severities", "Out", "Doubtful", "Fit", "Suspended"]
        selected_sev = st.selectbox("Severity Filter", severity_options, index=0)

    # 3. Hybrid Context Retrieval
    where_filter: Dict[str, Any] = {}
    if selected_cat != "All Categories":
        where_filter["category"] = selected_cat.lower().replace(" ", "_")
    if selected_sev != "All Severities":
        where_filter["severity"] = selected_sev.lower()

    where_filter_or_none = where_filter if where_filter else None

    # Run hybrid retrieval if search query provided
    hybrid_context = None
    if search_query.strip():
        hybrid_context = retriever.retrieve(query=search_query.strip(), top_k_news=12)
        results = hybrid_context.news_chunks
        # Apply where_filter in memory if Chroma query didn't filter
        if where_filter:
            filtered_results = []
            for item in results:
                meta = item.get("metadata", {})
                match = True
                for k, v in where_filter.items():
                    if k == "severity" and meta.get("category") == "injury" and meta.get("player_tags", "general") != "general":
                        continue  # Player-linked injury severity is filtered after FPL player resolution.
                    if meta.get(k) != v:
                        match = False
                        break
                if match:
                    filtered_results.append(item)
            results = filtered_results
    else:
        category_filter = {"category": where_filter["category"]} if "category" in where_filter else None
        result_limit = max(15, vector_store.count()) if selected_sev != "All Severities" else 15
        results = vector_store.get_recent(limit=result_limit, where_filter=category_filter)

    # 4. Display Structured Player Card if a Player was identified
    if hybrid_context and hybrid_context.target_player:
        player = hybrid_context.target_player
        st.markdown("### 👤 Structured Player Intelligence (Official SQLite Database)")

        chance = player.chance_of_playing_next_round if player.chance_of_playing_next_round is not None else (
            0 if player.status in ("i", "s", "u") else 100
        )

        with st.container(border=True):
            c_photo, c_info, c_fix = st.columns([1, 2.5, 2.5])
            with c_photo:
                photo_url = resolve_player_photo_url(player, repo)
                st.image(photo_url, caption=player.web_name, width=120)

            with c_info:
                st.markdown(f"#### **{sanitize_text(player.full_name)}** ({sanitize_text(player.web_name)})")
                st.markdown(
                    f"**Club:** {sanitize_text(player.team_name)}  |  **Position:** {player.position}  |  **Cost:** £{player.cost_float:.1f}m"
                )
                st.markdown(
                    f"**Form:** `{player.form}`  |  **Total Points:** `{player.total_points}`  |  **xG:** `{player.expected_goals:.2f}`  |  **xA:** `{player.expected_assists:.2f}`"
                )

                # Availability badge
                status_desc = STATUS_MAP.get(player.status, player.status.upper())
                if player.status in ("i", "s", "u") or chance == 0:
                    st.error(f"Status: {status_desc} ({chance}% chance of playing)")
                elif player.status == "d" or chance < 100:
                    st.warning(f"Status: {status_desc} ({chance}% chance of playing)")
                else:
                    st.success(f"Status: {status_desc} (100% available)")

                if player.news:
                    clean_news = sanitize_text(player.news)
                    news_time = sanitize_text(player.news_added or "Recent")
                    st.caption(f"🚨 **Official Bulletin:** {clean_news} *({news_time})*")

            with c_fix:
                st.markdown("**Upcoming Fixtures (FDR):**")
                if hybrid_context.upcoming_fixtures:
                    for fx in hybrid_context.upcoming_fixtures:
                        is_home = fx.team_h == player.team_id
                        opp_name = fx.team_a_short_name or fx.team_a_name if is_home else fx.team_h_short_name or fx.team_h_name
                        loc = "H" if is_home else "A"
                        fdr = fx.team_h_difficulty if is_home else fx.team_a_difficulty
                        fdr_label = FDR_LABELS.get(fdr, "")
                        fdr_emoji = "🟢" if fdr <= 2 else "⚪" if fdr == 3 else "🔴"
                        st.markdown(
                            f"- {fdr_emoji} **GW{fx.event}:** vs {sanitize_text(opp_name)} ({loc}) — `FDR {fdr} ({fdr_label})`"
                        )
                else:
                    st.info("No upcoming fixtures scheduled.")

        st.markdown("---")

    # 5. Display News & Injury Bulletins
    if search_query.strip():
        st.markdown(f"### 🔍 Retrieved Semantic News Bulletins for: *'{sanitize_text(search_query)}'*")
    else:
        st.markdown("### 📋 Latest Football News & Medical Bulletins")

    if not results:
        st.info("No news articles match the selected search and filters.")
        return

    # Category icon mapping
    category_icons = {
        "injury": "🚑 Injury",
        "press_conference": "🎙️ Press Conference",
        "transfer": "🔄 Transfer",
        "tactics": "📋 Tactics",
        "general_news": "📰 General News",
    }

    # Severity label mapping
    severity_labels = {
        "out": "🔴 OUT",
        "doubtful": "🟡 DOUBTFUL",
        "suspended": "🟠 SUSPENDED",
        "fit": "🟢 FIT",
        "unknown": "⚪ STATUS UNKNOWN",
    }

    displayed_results = 0
    for item in results:
        meta = item.get("metadata", {})
        raw_title = meta.get("title", "Football Update")
        raw_source = meta.get("source", "News Feed")
        raw_category = meta.get("category", "general_news").lower().replace(" ", "_")
        raw_severity = meta.get("severity", "unknown").lower()
        raw_published = str(meta.get("published_at", ""))[:10]
        raw_text = item.get("text", "")
        sim = item.get("similarity")
        raw_player_tags = meta.get("player_tags", "general")
        raw_team_tags = meta.get("team_tags", "general")

        # Sanitize all fields in Python before display
        clean_title = sanitize_text(raw_title)
        clean_source = sanitize_text(raw_source)
        clean_text = sanitize_text(raw_text)
        clean_published = sanitize_text(raw_published)
        clean_player_tags = sanitize_text(raw_player_tags)
        clean_team_tags = sanitize_text(raw_team_tags)

        cat_badge = category_icons.get(raw_category, f"📰 {sanitize_text(raw_category).title()}")
        sev_badge = severity_labels.get(raw_severity, f"⚪ {sanitize_text(raw_severity).upper()}")

        team_tag_names = [sanitize_text(tag).strip().casefold() for tag in str(raw_team_tags).split(",")]
        item_text = f"{raw_title} {raw_text}".casefold()
        linked_player = None
        for tag in str(raw_player_tags).split(","):
            cleaned_tag = sanitize_text(tag).strip()
            if cleaned_tag and cleaned_tag.casefold() != "general":
                candidates = players_by_tag.get(cleaned_tag.casefold(), [])
                if len(candidates) > 1:
                    by_team = [
                        candidate for candidate in candidates
                        if any(team_tag in {
                            (candidate.team_name or "").casefold(),
                            (candidate.team_short_name or "").casefold(),
                        } for team_tag in team_tag_names if team_tag)
                    ]
                    if len(by_team) == 1:
                        candidates = by_team
                    else:
                        by_full_name = [
                            candidate for candidate in candidates
                            if candidate.full_name.casefold() in item_text
                        ]
                        candidates = by_full_name if len(by_full_name) == 1 else []
                if len(candidates) == 1:
                    linked_player = candidates[0]
                    break

        is_injury_record = raw_category == "injury"
        if linked_player and is_injury_record:
            current_status_key = injury_severity_key(linked_player.status)
            sev_badge = severity_labels.get(current_status_key, sev_badge)
        if selected_sev != "All Severities":
            actual_severity = injury_severity_key(linked_player.status) if linked_player and is_injury_record else raw_severity
            if actual_severity != selected_sev.casefold():
                continue
        if displayed_results >= 15:
            break
        displayed_results += 1
        reconciliation = None
        if linked_player and is_injury_record:
            fixtures = repo.get_upcoming_fixtures(team_id=linked_player.team_id, limit=380)
            candidates = []
            if linked_player.news:
                candidates.append({
                    "source": "Premier League Official Medical Bulletin",
                    "date": linked_player.news_added,
                    "text": linked_player.news,
                    "link": "",
                })
            candidates.append({
                "source": raw_source,
                "date": meta.get("published_at", ""),
                "text": raw_text,
                "link": meta.get("link", ""),
            })
            reconciliation = reconcile_injury_sources(candidates, fixtures, linked_player.team_id)

        with st.container(border=True):
            col_badges, col_source = st.columns([1.2, 1])
            with col_badges:
                st.markdown(f"**{cat_badge}** &nbsp;•&nbsp; `{sev_badge}`")
            with col_source:
                match_str = f" &nbsp;•&nbsp; **Match:** `{sim*100:.1f}%`" if sim is not None else ""
                source_label = "Indexed record" if linked_player and is_injury_record else "Source"
                st.caption(f"**{source_label}:** {clean_source} ({clean_published}){match_str}")

            if linked_player:
                photo_col, article_col = st.columns([0.7, 6])
                with photo_col:
                    st.image(resolve_player_photo_url(linked_player, repo), width=64)
                content_col = article_col
            else:
                content_col = st.container()

            with content_col:
                if clean_title and not (linked_player and is_injury_record):
                    st.markdown(f"**{clean_title}**")

                if not (linked_player and is_injury_record):
                    st.write(clean_text)
                    st.caption(f"🏷️ **Player(s):** *{clean_player_tags}* &nbsp;|&nbsp; **Club(s):** *{clean_team_tags}*")

            if linked_player and is_injury_record:
                return_source = reconciliation["source"] if reconciliation else {}
                return_info = reconciliation["info"] if reconciliation else {}
                chance = linked_player.chance_of_playing_next_round
                if chance is None:
                    chance = 0 if linked_player.status in {"i", "s", "u"} else 100
                status_label = STATUS_MAP.get(linked_player.status, linked_player.status.upper())
                injury_problem = return_info.get("injury_problem") or "Not available"
                expected_return = return_info["expected_return_date"]
                return_text = expected_return.strftime("%d %b %Y").lstrip("0") if expected_return else "Not available"
                return_text += f" ({return_info['return_date_confidence']})" if expected_return else ""
                if not expected_return and return_info.get("return_period"):
                    return_text = return_info["return_period"]
                start_date = return_info["injury_start_date"]
                start_text = start_date.strftime("%d %b %Y").lstrip("0") if start_date else "Not available"
                recovery_text = "Not available"
                if return_info.get("recovery_days"):
                    day_low, day_high = return_info["recovery_days"]
                    week_low, week_high = return_info["recovery_weeks"]
                    days = f"~{day_low} days" if day_low == day_high else f"~{day_low}–{day_high} days"
                    weeks = f"~{week_low:g} weeks" if week_low == week_high else f"~{week_low:g}–{week_high:g} weeks"
                    recovery_text = f"{days} / {weeks}"
                elif expected_return and start_date:
                    days = max((expected_return - start_date).days, 0)
                    recovery_text = f"~{days} days / ~{days / 7:.1f} weeks"
                matchweek = return_info["matchweek"]
                source_mw = return_info.get("explicit_matchweek")
                matchweek_text = f"MW{matchweek} ({return_info['return_date_confidence']})" if matchweek else (
                    f"Source-reported MW{source_mw}" if source_mw else "Not available"
                )
                if reconciliation and reconciliation["conflict"]:
                    matchweek_text += " — Sources disagree"
                source_label = sanitize_text(str(return_source.get("source", "News Feed")))
                source_date_text = sanitize_text(str(return_source.get("date") or "Not available"))[:10]
                source_link = return_source.get("link", "")
                other_sources = list(dict.fromkeys(
                    sanitize_text(str(source.get("source", "News Feed")))
                    for source, _ in (reconciliation.get("sources", []) if reconciliation else [])
                    if source.get("source", "").casefold() != str(return_source.get("source", "")).casefold()
                ))

                st.markdown("**Return Information**")
                st.markdown(
                    f"- **Player:** {sanitize_text(linked_player.full_name or linked_player.web_name)}  \n"
                    f"- **Club:** {sanitize_text(linked_player.team_name or linked_player.team_short_name or 'Not available')}  \n"
                    f"- **Injury:** {sanitize_text(injury_problem)}  \n"
                    f"- **Availability:** {sanitize_text(status_label)} ({chance}% chance of playing)  \n"
                    f"- **Injury start:** {start_text}  \n"
                    f"- **Expected return:** {return_text}  \n"
                    f"- **Estimated recovery:** {recovery_text}  \n"
                    f"- **Expected Matchweek:** {matchweek_text}  \n"
                    f"- **Return status:** {return_info.get('return_date_confidence', 'Unknown')}  \n"
                    f"- **Source:** {source_label}  \n"
                    f"- **Source updated:** {source_date_text}  \n"
                    f"- **Last verified:** {reconciliation['last_verified_at'].strftime('%d %b %Y') if reconciliation else 'Not available'}"
                )
                if other_sources:
                    st.caption(f"Other indexed sources: {', '.join(other_sources)}")
                fitness_note = return_info.get("fitness_note") or return_info.get("relative_availability_note")
                if fitness_note:
                    st.caption(f"Source fitness update: {sanitize_text(fitness_note)}")
                if reconciliation and reconciliation["conflict"]:
                    st.warning("Sources disagree. The displayed source is the newest available source at the highest available priority.")
                if return_info.get("stale"):
                    st.caption("Selected return evidence is stale; check the source for a newer update.")
                if expected_return:
                    st.caption("Return date is source-reported and may change after further medical updates.")
                if isinstance(source_link, str) and source_link.startswith(("https://", "http://")):
                    st.link_button("View primary source", source_link)
                elif expected_return:
                    st.caption("Source URL: Not available in the indexed record.")

                with st.expander("View source evidence"):
                    if reconciliation:
                        for evidence, evidence_info in reconciliation["sources"]:
                            evidence_source = sanitize_text(str(evidence.get("source", "News Feed")))
                            evidence_date = sanitize_text(str(evidence.get("date") or "Not available"))[:10]
                            evidence_title = sanitize_text(str(evidence.get("title") or clean_title))
                            stale_label = " · STALE" if evidence_info.get("stale") else ""
                            st.markdown(f"**{evidence_source}** · {evidence_date}{stale_label}")
                            if evidence_title:
                                st.caption(evidence_title)
                            st.write(sanitize_text(evidence.get("text", "Not available")))
                            evidence_url = evidence.get("link", "")
                            if isinstance(evidence_url, str) and evidence_url.startswith(("https://", "http://")):
                                st.link_button("Open evidence source", evidence_url, key=f"injury_source_{item.get('id', raw_title)}_{evidence_source}")
