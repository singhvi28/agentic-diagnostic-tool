from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    target_app_root: Path = Path("./examples/target_app")
    error_log_path: Path = Path("./logs/app_errors.log")
    sandbox_root: Path = Path("./examples/target_app")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "diagnostic"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "fastapi-logs"
    qdrant_api_key: str | None = None

    max_retries: int = 3
    llm_model: str = "gpt-4o"


@lru_cache
def get_settings() -> Settings:
    return Settings()
