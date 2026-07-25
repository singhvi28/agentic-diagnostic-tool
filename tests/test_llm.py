"""LLM provider factory unit tests (no live API calls)."""

import pytest

from diagnostic_engine.config import Settings
from diagnostic_engine.llm.client import (
    CursorLLM,
    GeminiLLM,
    MissingCredentialsError,
    OpenAILLM,
    get_llm_client,
)


def test_openai_factory_selects_openai_client():
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-test",
        openai_model="gpt-4o",
        llm_model="",
    )
    client = get_llm_client(settings)
    assert isinstance(client, OpenAILLM)
    assert client.model == "gpt-4o"


def test_gemini_factory_defaults_to_flash_3():
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="gem-test",
        gemini_model="gemini-3-flash-preview",
        llm_model="",
    )
    client = get_llm_client(settings)
    assert isinstance(client, GeminiLLM)
    assert client.model == "gemini-3-flash-preview"


def test_cursor_factory_defaults_to_composer_2_5():
    settings = Settings(
        llm_provider="cursor",
        cursor_api_key="cur-test",
        cursor_model="composer-2.5",
        llm_model="",
    )
    client = get_llm_client(settings)
    assert isinstance(client, CursorLLM)
    assert client.model == "composer-2.5"


def test_llm_model_override():
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-test",
        llm_model="gpt-4o-mini",
    )
    assert settings.resolved_llm_model() == "gpt-4o-mini"
    client = get_llm_client(settings)
    assert client.model == "gpt-4o-mini"


def test_missing_credentials_raises():
    settings = Settings(llm_provider="gemini", gemini_api_key="")
    with pytest.raises(MissingCredentialsError):
        get_llm_client(settings)


def test_has_llm_credentials_per_provider():
    assert Settings(llm_provider="openai", openai_api_key="x").has_llm_credentials()
    assert not Settings(llm_provider="openai", openai_api_key="").has_llm_credentials()
    assert Settings(llm_provider="cursor", cursor_api_key="x").has_llm_credentials()
