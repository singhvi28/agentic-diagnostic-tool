"""Multi-provider LLM clients for diagnose/patch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from diagnostic_engine.config import Settings, get_settings


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str) -> LLMResponse: ...


class OpenAILLM:
    provider = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> LLMResponse:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=self.model, temperature=0, api_key=self.api_key)
        response = llm.invoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        return LLMResponse(content=content, provider=self.provider, model=self.model)


class GeminiLLM:
    provider = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def complete(self, system: str, user: str) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
            ),
        )
        content = getattr(response, "text", None) or str(response)
        return LLMResponse(content=content, provider=self.provider, model=self.model)


class CursorLLM:
    """Cursor SDK Agent.prompt using Composer (default composer-2.5)."""

    provider = "cursor"

    def __init__(self, api_key: str, model: str, cwd: str | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.cwd = cwd

    def complete(self, system: str, user: str) -> LLMResponse:
        from pathlib import Path

        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions

        cwd = self.cwd or str(Path.cwd())
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "IMPORTANT: Reply with a single JSON object only. "
            "Do not modify files; analysis/output only."
        )
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=self.api_key,
                model=self.model,
                local=LocalAgentOptions(cwd=cwd),
            ),
        )
        content = getattr(result, "result", None) or str(result)
        return LLMResponse(content=str(content), provider=self.provider, model=self.model)


class MissingCredentialsError(RuntimeError):
    pass


def get_llm_client(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    key = settings.active_llm_api_key()
    if not key:
        raise MissingCredentialsError(
            f"No API key for llm_provider={settings.llm_provider!r}. "
            f"Set the matching env var (OPENAI_API_KEY / GEMINI_API_KEY / CURSOR_API_KEY)."
        )
    model = settings.resolved_llm_model()
    if settings.llm_provider == "gemini":
        return GeminiLLM(api_key=key, model=model)
    if settings.llm_provider == "cursor":
        return CursorLLM(
            api_key=key,
            model=model,
            cwd=str(settings.sandbox_root.resolve()),
        )
    return OpenAILLM(api_key=key, model=model)
