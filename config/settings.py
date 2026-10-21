"""
Application Configuration and Environment Settings using Pydantic Settings.
"""

from pathlib import Path
from typing import Literal
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
    APP_NAME: str = "PL Fantasy Predictor"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    FPL_SCHEDULER_MODE: Literal["embedded", "external"] = "embedded"

    # Optional durable Postgres backend for hosted deployments. When unset, local
    # development continues to use the SQLite database below.
    DATABASE_URL: str | None = None

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
    NEWS_REFRESH_INTERVAL_HOURS: int = Field(default=6, ge=1)
    MAX_NEWS_ITEMS_PER_FEED: int = Field(default=25, ge=1)
    CHUNK_SIZE: int = 400
    CHUNK_OVERLAP: int = 60


# Global singleton instance
settings = Settings()

# Ensure directories exist
settings.SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
settings.CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
settings.RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
