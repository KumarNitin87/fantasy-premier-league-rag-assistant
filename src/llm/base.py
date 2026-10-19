"""
Abstract Base Class for LLM Providers.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseLLMProvider(ABC):
    """Abstract interface for natural-language generation providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the LLM provider."""
        pass

    @abstractmethod
    def generate_response(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Generate response given a user prompt and optional system instructions."""
        pass
