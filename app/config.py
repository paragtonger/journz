from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    app_name: str = "Journz"
    debug: bool = False
    database_url: str = "sqlite:///data/journz.db"

    model_config = SettingsConfigDict(
        env_prefix="JOURNZ_",
        env_file=".env",
        extra="ignore",
    )

    @property
    def database_path(self) -> Path:
        """Return the local SQLite path, when the configured URL is SQLite."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url.removeprefix(prefix))
        return Path("data/journz.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
