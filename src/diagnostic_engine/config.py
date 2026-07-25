from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "gemini", "cursor"]
EmbeddingProvider = Literal["hash", "openai", "gemini"]
McpTransport = Literal["stdio", "http", "inprocess"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM API keys (set whichever providers you use)
    openai_api_key: str = ""
    gemini_api_key: str = ""
    cursor_api_key: str = ""

    # Active LLM: openai | gemini | cursor
    llm_provider: LLMProvider = "openai"
    # Defaults per provider (override with LLM_MODEL)
    llm_model: str = ""
    openai_model: str = "gpt-4o"
    gemini_model: str = "gemini-3-flash-preview"
    cursor_model: str = "composer-2.5"

    target_app_root: Path = Path("./examples/target_app")
    target_app_import: str = "examples.target_app.main:app"
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
    embedding_provider: EmbeddingProvider = "hash"

    max_retries: int = 3

    # LangGraph → FastMCP transport
    # stdio: agent spawns diagnostic-mcp; http: connect to MCP_URL; inprocess: tests only
    mcp_transport: McpTransport = "stdio"
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    mcp_stdio_command: str = "diagnostic-mcp"
    mcp_http_host: str = "127.0.0.1"
    mcp_http_port: int = 8000

    def resolved_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        if self.llm_provider == "gemini":
            return self.gemini_model
        if self.llm_provider == "cursor":
            return self.cursor_model
        return self.openai_model

    def active_llm_api_key(self) -> str:
        if self.llm_provider == "gemini":
            return self.gemini_api_key
        if self.llm_provider == "cursor":
            return self.cursor_api_key
        return self.openai_api_key

    def has_llm_credentials(self) -> bool:
        return bool(self.active_llm_api_key())


@lru_cache
def get_settings() -> Settings:
    return Settings()
