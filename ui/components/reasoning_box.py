"""
UI Component: Structured Explanation and LLM Commentary Box.
"""

import streamlit as st


def render_tactical_commentary(commentary_markdown: str, provider_name: str = "Gemini"):
    """Render an authoritative AI tactical analysis box with custom styling."""
    st.markdown(
        f"""
        <div style="background-color: #161b22; border-left: 4px solid #58a6ff; padding: 16px 20px; border-radius: 6px; margin-top: 15px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <span style="font-weight: bold; color: #58a6ff; font-size: 0.95rem;">🤖 AI Tactical Intelligence & Grounded Reasoning</span>
                <span style="color: #8b949e; font-size: 0.8rem; background: #21262d; padding: 2px 8px; border-radius: 4px;">Powered by {provider_name}</span>
            </div>
            <div style="color: #e6edf3; font-size: 0.95rem; line-height: 1.5;">
                {commentary_markdown}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
