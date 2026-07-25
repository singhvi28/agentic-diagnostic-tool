from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    patch_backup_root: Path = Path("./patches")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "diagnostic"

    database_url: str = (
        "postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic"
    )
    embedding_dim: int = 384
    embedding_provider: Literal["hash", "openai"] = "hash"

    max_retries: int = 3
    llm_model: str = "gpt-4o"


@lru_cache
def get_settings() -> Settings:
    return Settings()
