"""
Offline Mock LLM Provider: Generates grounded tactical football explanations using retrieved context.
"""

from typing import Optional
import re
from src.llm.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """Deterministic offline provider simulating LLM analysis for demos and tests."""

    @property
    def name(self) -> str:
        return "Offline Mock Reasoner"

    def generate_response(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Synthesize a structured tactical response based on the injected prompt context."""
        has_news = "Verified Football News" in prompt
        has_player = "Structured Player Data" in prompt

        # Extract player name if in context
        player_match = re.search(r"Name:\s*([A-Za-z\s]+)", prompt)
        player_name = player_match.group(1).strip() if player_match else "the requested player"

        # Check for injury keywords
        is_injured = "INJURED" in prompt or "DOUBTFUL" in prompt or "chance of playing" in prompt.lower()

        lines = [
            f"### 🎯 Tactical Analysis & Recommendation: **{player_name}**",
            "",
        ]

        if is_injured:
            lines.extend([
                "⚠️ **Fitness & Availability Alert:**",
                f"Official bulletins and news indicate **{player_name}** is carrying a knock or doubtful status. "
                "Immediate caution is strongly advised before committing a transfer or handing them the captain's armband.",
                "",
            ])

        lines.extend([
            "📊 **Statistical & Fixture Outlook:**",
            f"Evaluating the underlying statistical trends, **{player_name}** presents an intriguing profile. "
            "When analyzing expected goal involvements ($xGI$) and upcoming Fixture Difficulty Ratings (FDR), "
            "their ceiling depends on team tactical setup and opponent defensive vulnerability.",
            "",
            "💡 **Manager Recommendation:**",
            f"- If you already own **{player_name}**, hold until the latest manager press conference updates are confirmed.",
            f"- If considering as an incoming transfer, compare their upcoming 3-game FDR against alternative budget options in our Transfer Advisor.",
            "",
            "ℹ️ *Note: Running in offline mock reasoning mode. To enable real-time dynamic Gemini explanations, provide your `GEMINI_API_KEY` in the sidebar or `.env`.*",
        ])

        return "\n".join(lines)
