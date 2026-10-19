"""
Application Configuration and Environment Settings using Pydantic Settings.
"""

from pathlib import Path
from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base Project Root Directory
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """System-wide configuration settings with environment variable overrides."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Project Information
    APP_NAME: str = "Fantasy Football & Premier League RAG Assistant"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Primary LLM Provider
    LLM_PROVIDER: Literal["gemini", "openai", "groq", "ollama", "mock"] = "gemini"

    # API Keys & Models
    GEMINI_API_KEY: Optional[str] = Field(default=None)
    GEMINI_MODEL: str = "gemini-1.5-flash"

    OPENAI_API_KEY: Optional[str] = Field(default=None)
    OPENAI_MODEL: str = "gpt-4o-mini"

    GROQ_API_KEY: Optional[str] = Field(default=None)
    GROQ_MODEL: str = "llama-3.1-70b-versatile"

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3"

    # Offline / Mock Fallback Flag
    OFFLINE_MOCK_MODE: bool = False
    FPL_SCHEDULER_MODE: Literal["embedded", "external"] = "embedded"

    # Storage Paths
    SQLITE_DB_PATH: Path = BASE_DIR / "data" / "fpl_historical.db"
    CHROMA_PERSIST_DIR: Path = BASE_DIR / "data" / "chroma_data"
    RAW_DATA_DIR: Path = BASE_DIR / "data" / "raw"

    # Embedding Settings
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    CHROMA_COLLECTION_NAME: str = "football_news"

    # FPL API Settings
    FPL_API_BASE_URL: str = "https://fantasy.premierleague.com/api"
    FPL_TIMEOUT_SECONDS: int = 15
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_FACTOR: float = 1.0

    # News Ingestion Settings
    NEWS_REFRESH_INTERVAL_HOURS: int = 6
    MAX_NEWS_ITEMS_PER_FEED: int = 25
    CHUNK_SIZE: int = 400
    CHUNK_OVERLAP: int = 60


# Global singleton instance
settings = Settings()

# Ensure directories exist
settings.SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
settings.CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
settings.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
