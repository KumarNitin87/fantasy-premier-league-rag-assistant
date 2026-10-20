"""
PL Fantasy Predictor
Main Streamlit Application Entrypoint.
"""

import streamlit as st
from pathlib import Path
import time

# Streamlit Page Configuration
st.set_page_config(
    page_title="PL Fantasy Predictor",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling for Premier League Modern Dashboard Theme
st.markdown("""
<style>
    :root {
        --fpl-accent: #00ff87;
    }
    html, body, [data-testid="stAppViewContainer"] {
        background: #080d0c;
    }
    /* Full-viewport stadium layers sit behind Streamlit's responsive layout. */
    [data-testid="stAppViewContainer"] {
        position: relative;
        isolation: isolate;
    }
    [data-testid="stAppViewContainer"]::before {
        content: "";
        position: fixed;
        inset: 0;
        z-index: 0;
        pointer-events: none;
        background:
            radial-gradient(ellipse at 18% 2%, rgba(197, 255, 222, 0.16), transparent 23%),
            radial-gradient(ellipse at 81% 1%, rgba(164, 255, 207, 0.13), transparent 22%),
            radial-gradient(ellipse at 50% 93%, rgba(0, 255, 135, 0.10), transparent 44%),
            radial-gradient(circle at 13% 18%, rgba(211, 255, 229, 0.12) 0 1px, transparent 2px),
            radial-gradient(circle at 73% 24%, rgba(211, 255, 229, 0.11) 0 1px, transparent 2px),
            radial-gradient(circle at 92% 39%, rgba(211, 255, 229, 0.08) 0 1px, transparent 2px),
            linear-gradient(180deg, #080d0c 0%, #0a1210 52%, #07100d 100%);
        animation: stadium-glow 18s ease-in-out infinite alternate;
    }
    /* Perspective pitch markings add depth without an image or WebGL dependency. */
    [data-testid="stAppViewContainer"]::after {
        content: "";
        position: fixed;
        z-index: 0;
        pointer-events: none;
        width: min(1120px, 94vw);
        height: min(680px, 68vh);
        left: 50%;
        bottom: 7vh;
        transform: translateX(-50%) perspective(900px) rotateX(61deg);
        transform-origin: center bottom;
        border: 1px solid rgba(156, 255, 197, 0.17);
        border-radius: 18px;
        background-color: rgba(8, 43, 28, 0.22);
        background-image:
            linear-gradient(90deg, transparent 49.8%, rgba(194, 255, 218, 0.16) 50%, transparent 50.2%),
            linear-gradient(0deg, transparent 49.8%, rgba(194, 255, 218, 0.14) 50%, transparent 50.2%),
            radial-gradient(ellipse at 50% 50%, transparent 0 12.7%, rgba(194, 255, 218, 0.15) 12.9% 13.1%, transparent 13.3%),
            radial-gradient(ellipse 42% 22% at 50% 0%, transparent 98.8%, rgba(194, 255, 218, 0.14) 99.2% 100%, transparent 100.4%),
            radial-gradient(ellipse 42% 22% at 50% 100%, transparent 98.8%, rgba(194, 255, 218, 0.13) 99.2% 100%, transparent 100.4%),
            repeating-linear-gradient(90deg, rgba(0, 255, 135, 0.025) 0 10%, transparent 10% 20%);
        box-shadow: 0 0 90px rgba(0, 255, 135, 0.08), inset 0 0 70px rgba(0, 255, 135, 0.035);
    }
    @keyframes stadium-glow {
        from { opacity: 0.88; }
        to { opacity: 1; }
    }
    @media (prefers-reduced-motion: reduce) {
        [data-testid="stAppViewContainer"]::before { animation: none; }
    }
    @media (max-width: 700px) {
        [data-testid="stAppViewContainer"]::after {
            width: 94vw;
            height: 54vh;
            bottom: 4vh;
        }
    }
    .stMain {
        position: relative;
        z-index: 1;
        container-name: fpl-main-pane;
        container-type: inline-size;
        background: transparent !important;
    }
    @container fpl-main-pane (max-width: 640px) {
        .stMain [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        .stMain [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
            flex: 1 1 100% !important;
            width: 100% !important;
            max-width: 100% !important;
        }
        .stMain [data-testid="stImageContainer"] {
            max-width: 100%;
        }
        .stMain [data-testid="stImageContainer"] img {
            max-width: 100% !important;
            height: auto !important;
        }
    }
    [data-testid="stMainBlockContainer"] {
        position: relative;
        z-index: 1;
    }
    section[data-testid="stSidebar"] {
        z-index: 2;
        background: linear-gradient(180deg, #171c1c 0%, #111716 100%);
    }
    [data-testid="stSidebar"] > div:first-child {
        height: 100vh;
        overflow-y: auto;
        scrollbar-width: thin;
        scrollbar-color: rgba(0, 255, 135, 0.28) transparent;
    }
    [data-testid="stSidebar"] [role="radiogroup"] {
        gap: 0.18rem !important;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"] {
        min-height: 42px;
        display: flex;
        align-items: center;
        box-sizing: border-box;
        border: 1px solid rgba(207, 232, 220, 0.055);
        border-left: 3px solid transparent;
        border-radius: 9px;
        padding: 0.48rem 0.62rem;
        margin: 0.07rem 0;
        white-space: nowrap;
        background: linear-gradient(110deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.012));
        transition: background-color 150ms ease, border-color 150ms ease, transform 150ms ease;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"] > div {
        width: 100%;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"] > div > div:first-child {
        display: none !important;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"] p {
        margin: 0;
        color: #d8e2dc;
        font-size: 0.91rem;
        font-weight: 520;
        line-height: 1.25rem;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"]:hover {
        background: rgba(255, 255, 255, 0.075);
        border-color: rgba(207, 232, 220, 0.13);
        transform: translateX(2px);
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) {
        background: linear-gradient(100deg, rgba(0, 255, 135, 0.16), rgba(0, 255, 135, 0.045));
        border-color: rgba(0, 255, 135, 0.18);
        border-left-color: var(--fpl-accent);
        box-shadow: inset 0 0 22px rgba(0, 255, 135, 0.035);
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"]:has(input:checked) p {
        color: #f1fff7;
        font-weight: 650;
    }
    [data-testid="stSidebar"] label[data-testid="stRadioOption"]:focus-within {
        outline: 2px solid rgba(0, 255, 135, 0.55);
        outline-offset: 2px;
    }
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        border: 1px solid rgba(207, 232, 220, 0.09);
        border-radius: 9px;
        background: rgba(255, 255, 255, 0.025);
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        color: var(--fpl-accent);
    }
    [data-testid="stMetric"] {
        background: rgba(6, 17, 13, 0.58);
        border: 1px solid rgba(167, 225, 190, 0.10);
        border-radius: 10px;
        padding: 0.65rem 0.8rem;
        backdrop-filter: blur(5px);
    }
</style>
""", unsafe_allow_html=True)

# Import Backend Singletons
from config.settings import settings
from src.database.repository import FPLRepository
from src.database.connection import init_db
from src.rag.vector_store import ChromaVectorStore
from src.rag.retriever import HybridRetriever
from src.analytics.scoring import DecisionScoringEngine
from src.analytics.transfer_eval import TransferAnalyzer
from src.analytics.captain_picker import CaptainPicker
from src.ingestion.sync_fpl import FPLSyncPipeline
from src.ingestion.sync_news import NewsIngestionPipeline
from src.sync.auto_scheduler import FPLAutoScheduler, detect_matchday

# Initialize Cached Singletons
@st.cache_resource
def get_repository() -> FPLRepository:
    init_db(settings.SQLITE_DB_PATH)
    return FPLRepository()


@st.cache_resource
def get_vector_store() -> ChromaVectorStore:
    return ChromaVectorStore()


@st.cache_resource
def get_scoring_engine() -> DecisionScoringEngine:
    return DecisionScoringEngine(team_provider=get_repository().get_all_teams)


@st.cache_resource
def get_transfer_analyzer() -> TransferAnalyzer:
    return TransferAnalyzer(scoring_engine=get_scoring_engine(), repository=get_repository())


@st.cache_resource
def get_captain_picker() -> CaptainPicker:
    return CaptainPicker(repository=get_repository(), scoring_engine=get_scoring_engine())


@st.cache_resource
def get_scheduler() -> FPLAutoScheduler:
    """Return the process-wide FPL auto-sync scheduler (one per Streamlit server)."""
    _repo = get_repository()

    def _sync_callback():
        pipeline = FPLSyncPipeline(repository=_repo)
        result = pipeline.sync_all(
            sync_player_history_top_n=30,
            force_refresh=True,
            allow_fallback=False,
        )
        get_scoring_engine().refresh_team_context()
        return result

    def _matchday_detector():
        return detect_matchday(_repo)

    def _last_sync_provider():
        stats = _repo.get_db_stats()
        return stats.get("last_sync")

    def _on_sync_success(_result):
        get_scoring_engine().refresh_team_context()
        st.cache_data.clear()

    def _sync_news_callback():
        return NewsIngestionPipeline(
            vector_store=get_vector_store(),
            repository=_repo,
        ).run(max_per_feed=settings.MAX_NEWS_ITEMS_PER_FEED)

    def _last_news_attempt_provider():
        return _repo.get_db_stats().get("last_news_attempt_at")

    def _on_news_sync_success(_result):
        st.cache_data.clear()

    scheduler = FPLAutoScheduler(
        sync_callback=_sync_callback,
        matchday_detector=_matchday_detector,
        last_sync_provider=_last_sync_provider,
        on_sync_success=_on_sync_success,
        news_sync_callback=_sync_news_callback,
        last_news_sync_provider=_last_news_attempt_provider,
        news_interval_seconds=settings.NEWS_REFRESH_INTERVAL_HOURS * 60 * 60,
        on_news_sync_success=_on_news_sync_success,
    )
    if settings.FPL_SCHEDULER_MODE == "embedded":
        scheduler.start()
    else:
        scheduler.status["mode"] = "external"
    return scheduler


repo = get_repository()
vector_store = get_vector_store()
scoring_engine = get_scoring_engine()
transfer_analyzer = get_transfer_analyzer()
captain_picker = get_captain_picker()
retriever = HybridRetriever(vector_store=vector_store, repository=repo)
scheduler = get_scheduler()  # ensures background thread starts on first load

if "_visible_fpl_attempt_at" not in st.session_state:
    st.session_state["_visible_fpl_attempt_at"] = repo.get_db_stats().get("last_fpl_attempt_at")
if "_visible_news_attempt_at" not in st.session_state:
    st.session_state["_visible_news_attempt_at"] = repo.get_db_stats().get("last_news_attempt_at")


@st.fragment(run_every="30s")
def _refresh_after_fpl_sync():
    """Rerun the dashboard when a background sync commits FPL or news data."""
    sync_status = repo.get_db_stats()
    latest_fpl_attempt = sync_status.get("last_fpl_attempt_at")
    latest_news_attempt = sync_status.get("last_news_attempt_at")
    fpl_updated = latest_fpl_attempt and latest_fpl_attempt != st.session_state.get("_visible_fpl_attempt_at")
    news_updated = latest_news_attempt and latest_news_attempt != st.session_state.get("_visible_news_attempt_at")
    if fpl_updated or news_updated:
        st.session_state["_visible_fpl_attempt_at"] = latest_fpl_attempt
        st.session_state["_visible_news_attempt_at"] = latest_news_attempt
        st.cache_data.clear()
        st.rerun()


_refresh_after_fpl_sync()

# =============================================================================
# Sidebar: Navigation, maintenance controls, and collapsed settings
# =============================================================================
PAGE_OPTIONS = [
    "📊 Player Stats",
    "🗓️ Fixtures & FDR",
    "⚔️ Player Comparison",
    "🔄 Transfer & Captain Advisor",
    "📰 News & Injury Radar",
    "📈 Dashboard",
    "🏆 My Team",
]

with st.sidebar:
    st.markdown("## ⚽ PL Fantasy Predictor")
    st.caption("Fantasy Football Decision Support")
    st.markdown("---")
    st.markdown("#### NAVIGATION")
    selected_page = st.radio(
        "Dashboard pages",
        PAGE_OPTIONS,
        index=0,
        key="dashboard_navigation",
        label_visibility="collapsed",
    )

    st.markdown("---")
    with st.expander("🔄 Data Sync & Status", expanded=False):
        # ---- Auto-sync status banner ----
        sch_status = scheduler.status
        if settings.FPL_SCHEDULER_MODE == "external":
            heartbeat = settings.SQLITE_DB_PATH.parent / ".fpl_scheduler_heartbeat"
            if heartbeat.exists():
                heartbeat_age = time.time() - heartbeat.stat().st_mtime
                if heartbeat_age < 90:
                    st.caption(f"🤖 External FPL worker: healthy · heartbeat {int(heartbeat_age)}s ago")
                else:
                    st.warning(f"External FPL worker heartbeat is stale ({int(heartbeat_age)}s).")
            else:
                st.warning("External FPL worker heartbeat not found.")
        else:
            if sch_status.get("syncing_now"):
                st.info("⏳ Auto-sync in progress…")
            else:
                mode_label = sch_status.get("mode", "unknown")
                next_in = sch_status.get("next_sync_in", "—")
                last_auto = sch_status.get("last_sync_at")
                if last_auto:
                    try:
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(last_auto)
                        ago = int((datetime.now(timezone.utc) - ts).total_seconds() // 60)
                        st.caption(f"🤖 Auto-sync: last {ago}m ago · mode **{mode_label}** · next in **{next_in}**")
                    except Exception:
                        st.caption(f"🤖 Auto-sync active · mode **{mode_label}** · next in **{next_in}**")
                else:
                    st.caption(f"🤖 Auto-sync active · mode **{mode_label}** · next in **{next_in}**")

        if sch_status.get("last_error"):
            st.caption(f"⚠️ Last auto-sync error: {sch_status['last_error']}")

        # ---- Manual FPL refresh ----
        sync_blocked = sch_status.get("syncing_now", False)
        if st.button(
            "⏸️ Sync running…" if sync_blocked else "🚀 Sync Fresh FPL Data Now",
            use_container_width=True,
            disabled=sync_blocked,
            key="manual_fpl_sync_btn",
        ):
            with st.spinner("Syncing latest clubs, rosters, and fixtures from Premier League…"):
                try:
                    if scheduler.run_sync_now():
                        res = scheduler.status.get("last_result", {})
                        st.session_state["_visible_fpl_attempt_at"] = repo.get_db_stats().get("last_fpl_attempt_at")
                        st.success(f"✅ Synced {res.get('players', 0)} players & {res.get('fixtures', 0)} fixtures!")
                        st.rerun()
                    else:
                        st.warning("A sync is already in progress. Wait for it to finish, then refresh.")
                except Exception as exc:
                    st.error(f"FPL sync failed: {exc}. Existing data preserved.")

        news_syncing = scheduler.news_status.get("syncing_now", False)
        if st.button(
            "⏸️ News sync running…" if news_syncing else "📰 Fetch Latest News & Injuries",
            use_container_width=True,
            disabled=news_syncing,
            key="manual_news_sync_btn",
        ):
            with st.spinner("Fetching news and updating vector embeddings…"):
                try:
                    completed = scheduler.run_news_now()
                    result = scheduler.news_status.get("last_result", {})
                    if result.get("status") == "error":
                        st.error(f"News sync reported an upstream failure: {result.get('error', 'No configured feed succeeded')}")
                    elif result.get("status") == "partial":
                        st.warning(
                            f"News sync indexed {result.get('indexed_chunks', 0)} chunks, "
                            f"but some feeds failed: {result.get('error', 'Check sync status')}"
                        )
                    elif completed:
                        st.success(f"Indexed {result.get('indexed_chunks', 0)} fresh news chunks!")
                    else:
                        st.warning("A news sync is already in progress.")
                    if result.get("indexed_chunks", 0):
                        st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"News sync failed: {exc}")

        st.markdown("**Database & Ingestion Status**")
        st.caption(f"Players in DB: {len(repo.get_players(limit=700))}")
        sync_status = repo.get_db_stats()

        # FPL sync status
        fpl_sync_time = sync_status.get('last_sync', 'Never')
        fpl_status = sync_status.get('last_fpl_status', 'Never run')
        fpl_err = sync_status.get('last_fpl_error', '')
        st.markdown(f"**FPL Sync:** `{fpl_sync_time}`")
        st.caption(f"Last FPL attempt: `{sync_status.get('last_fpl_attempt_at') or 'Never'}`")
        if fpl_status == 'error':
            st.caption(f"⚠️ Last FPL attempt error: {fpl_err}")
        elif fpl_status == 'ok':
            st.caption("✅ FPL status: Healthy")

        # News sync status
        news_sync_time = sync_status.get('last_news_sync', 'Never')
        news_status = sync_status.get('last_news_status', 'Never run')
        news_err = sync_status.get('last_news_error', '')
        st.markdown(f"**News Sync:** `{news_sync_time}`")
        st.caption(f"Last news attempt: `{sync_status.get('last_news_attempt_at') or 'Never'}`")
        if news_status in ('error', 'partial'):
            st.caption(f"⚠️ Last News attempt error: {news_err}")
        elif news_status == 'ok':
            st.caption("✅ News status: Healthy")

        st.caption(f"News vectors: {vector_store.count()}")
        if settings.FPL_SCHEDULER_MODE == "external":
            st.caption(
                f"ℹ️ Worker schedule: FPL every 15 min on matchdays / 6 h otherwise; "
                f"news every {settings.NEWS_REFRESH_INTERVAL_HOURS} h."
            )
        else:
            st.caption(
                "ℹ️ Auto-sync: every 15 min on matchdays, 6 h otherwise. "
                f"News and injury ingestion runs every {settings.NEWS_REFRESH_INTERVAL_HOURS} h. "
                "Sync stops when the Streamlit server process exits."
            )

# =============================================================================
# Render the selected existing page (no duplicate page implementations)
# =============================================================================
try:
    if selected_page == PAGE_OPTIONS[0]:
        from ui.views.stats_explorer import render_stats_explorer
        render_stats_explorer(repo=repo, scoring_engine=scoring_engine)
    elif selected_page == PAGE_OPTIONS[1]:
        from ui.views.fixtures_view import render_fixtures_view
        render_fixtures_view(repo=repo)
    elif selected_page == PAGE_OPTIONS[2]:
        from ui.views.comparison_view import render_comparison_view
        render_comparison_view(repo=repo, scoring_engine=scoring_engine)
    elif selected_page == PAGE_OPTIONS[3]:
        from ui.views.transfer_advisor import render_transfer_advisor
        render_transfer_advisor(
            repo=repo,
            scoring_engine=scoring_engine,
            transfer_analyzer=transfer_analyzer,
            captain_picker=captain_picker,
        )
    elif selected_page == PAGE_OPTIONS[4]:
        from ui.views.news_feed import render_news_feed
        render_news_feed(vector_store=vector_store, repo=repo, retriever=retriever)
    elif selected_page == PAGE_OPTIONS[5]:
        from ui.views.phase2_dashboard import render_phase2_dashboard
        render_phase2_dashboard(repo=repo, scoring_engine=scoring_engine)
    elif selected_page == PAGE_OPTIONS[6]:
        from ui.views.my_team import render_my_team
        render_my_team(
            repo=repo,
            scoring_engine=scoring_engine,
            transfer_analyzer=transfer_analyzer,
            captain_picker=captain_picker,
            vector_store=vector_store,
            retriever=retriever,
        )
except ImportError as exc:
    if "Application Control policy has blocked" in str(exc):
        st.error(
            "Windows Application Control blocked a required native Python package for this page. "
            "Ask your administrator for an approved package/environment. The sidebar remains available "
            "so you can navigate to pages that do not require the blocked package."
        )
    else:
        raise
