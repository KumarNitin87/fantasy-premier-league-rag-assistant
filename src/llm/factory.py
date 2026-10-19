"""
Factory for creating and configuring LLM providers dynamically.
"""

import logging
from typing import Optional
from config.settings import settings
from src.llm.base import BaseLLMProvider
from src.llm.gemini_provider import GeminiLLMProvider
from src.llm.mock_provider import MockLLMProvider

logger = logging.getLogger(__name__)


def get_llm_provider(
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
) -> BaseLLMProvider:
    """Instantiate and return the appropriate LLM provider."""
    provider = (provider_name or settings.LLM_PROVIDER).lower()

    if settings.OFFLINE_MOCK_MODE or provider == "mock":
        return MockLLMProvider()

    if provider == "gemini":
        key = api_key or settings.GEMINI_API_KEY
        if not key:
            logger.info("GEMINI_API_KEY is not set. Defaulting to MockLLMProvider.")
            return MockLLMProvider()
        return GeminiLLMProvider(api_key=key, model_name=model_name or settings.GEMINI_MODEL)

    # Fallback to mock for unimplemented external providers
    logger.info("Provider '%s' selected. If keys are missing, mock provider is used.", provider)
    return MockLLMProvider()
