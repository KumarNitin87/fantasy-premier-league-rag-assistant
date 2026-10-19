"""
Google Gemini LLM Provider implementation using the official Google GenAI SDK.
"""

import logging
from typing import Optional
from config.settings import settings
from src.llm.base import BaseLLMProvider
from src.llm.mock_provider import MockLLMProvider

logger = logging.getLogger(__name__)


class GeminiLLMProvider(BaseLLMProvider):
    """Integrates Google Gemini for natural-language football reasoning."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name or settings.GEMINI_MODEL
        self._client = None
        self._fallback = MockLLMProvider()

    @property
    def name(self) -> str:
        return f"Google Gemini ({self.model_name})"

    def _get_client(self):
        """Initialize Google GenAI client."""
        if not self.api_key:
            return None

        if self._client is None:
            try:
                # Use official google-genai SDK
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning("Could not initialize google.genai: %s. Trying google.generativeai...", e)
                try:
                    import google.generativeai as legacy_genai
                    legacy_genai.configure(api_key=self.api_key)
                    self._client = "legacy"
                except Exception as ex:
                    logger.error("Failed to initialize Gemini client: %s", ex)
                    self._client = None
        return self._client

    def generate_response(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Call Gemini to generate reasoning, falling back to mock provider if key is absent."""
        if not self.api_key:
            logger.info("No GEMINI_API_KEY provided; using deterministic mock provider.")
            return self._fallback.generate_response(prompt, system_instruction)

        client = self._get_client()
        if client is None:
            return self._fallback.generate_response(prompt, system_instruction)

        try:
            full_prompt = prompt
            if system_instruction:
                full_prompt = f"System Instructions:\n{system_instruction}\n\nUser Query & Context:\n{prompt}"

            if client == "legacy":
                import google.generativeai as legacy_genai
                model = legacy_genai.GenerativeModel(self.model_name)
                response = model.generate_content(full_prompt)
                return response.text.strip()
            else:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt,
                )
                return response.text.strip()

        except Exception as e:
            logger.error("Gemini API call failed: %s. Falling back to mock response.", e)
            return (
                f"⚠️ *Gemini API call encountered an error: {e}. Displaying offline analysis:*\n\n"
                + self._fallback.generate_response(prompt, system_instruction)
            )
