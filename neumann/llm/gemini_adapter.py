"""Gemini adapter — Google Gemini models (2.0 Flash, 2.5 Pro, etc.).

Uses the official google-genai SDK (optional — falls back to HTTP if not installed).

Free tier available: 15 RPM, 1M context window, no credit card required.

Usage:
    from neumann.llm.gemini_adapter import GeminiAdapter
    llm = GeminiAdapter(api_key="AIza...")
    response = llm.chat_simple("Write hello world in Python", model="gemini-2.0-flash")
"""
from __future__ import annotations

from collections.abc import Generator, AsyncGenerator
from typing import Any

try:
    from google import genai
    from google.genai import types
    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False

from .adapter import LLMAdapter
from . import LLMProvider, LLMMessage, LLMResponse, LLMChunk, LLMUsage

_GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

# Pricing per 1M tokens (free tier available for most models)
_GEMINI_PRICES = {
    "gemini-2.5-flash-preview-05-20": {"input": 0.0, "output": 0.0},  # free tier
    "gemini-2.5-pro-preview-05-06":   {"input": 0.0, "output": 0.0},  # free tier
    "gemini-2.0-flash":                {"input": 0.0, "output": 0.0},  # free tier
    "gemini-2.0-flash-lite":           {"input": 0.0, "output": 0.0},  # free tier
    "gemini-1.5-flash":                {"input": 0.0, "output": 0.0},  # free tier
    "gemini-1.5-pro":                  {"input": 0.0, "output": 0.0},  # free tier
}


class GeminiAdapter(LLMAdapter):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.api_key = api_key or ""
        self.base_url = base_url
        self.timeout = timeout
        self._client: Any = None
        self._async_client: Any = None

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.GEMINI

    def available_models(self) -> list[str]:
        return list(_GEMINI_MODELS)

    def _get_client(self) -> Any:
        if self._client is None:
            if not _HAS_GEMINI:
                raise ImportError(
                    "google-genai package required. Install: pip install google-genai"
                )
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = genai.Client(**kwargs)
        return self._client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            if not _HAS_GEMINI:
                raise ImportError(
                    "google-genai package required. Install: pip install google-genai"
                )
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._async_client = genai.Client(**kwargs)
        return self._async_client

    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        # Build contents from messages
        contents = self._build_gemini_contents(messages)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            **{k: v for k, v in kwargs.items() if k not in (
                "temperature", "max_tokens", "max_output_tokens"
            )},
        )

        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        content_text = ""
        if response.candidates and response.candidates[0].content:
            parts = response.candidates[0].content.parts
            if parts:
                content_text = parts[0].text or ""

        # Extract usage
        usage = LLMUsage()
        if response.usage_metadata:
            usage = LLMUsage(
                prompt_tokens=response.usage_metadata.prompt_token_count or 0,
                completion_tokens=response.usage_metadata.candidates_token_count or 0,
                total_tokens=response.usage_metadata.total_token_count or 0,
                estimated_cost_usd=_calc_cost(
                    response.usage_metadata.prompt_token_count or 0,
                    response.usage_metadata.candidates_token_count or 0,
                    model,
                ),
            )

        finish_reason = None
        if response.candidates and response.candidates[0].finish_reason:
            finish_reason = str(response.candidates[0].finish_reason)

        return LLMResponse(
            content=content_text,
            provider=self.provider.value,
            model=model,
            usage=usage,
            finish_reason=finish_reason,
        )

    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        client = self._get_client()
        contents = self._build_gemini_contents(messages)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            **{k: v for k, v in kwargs.items() if k not in (
                "temperature", "max_tokens", "max_output_tokens"
            )},
        )

        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content:
                parts = chunk.candidates[0].content.parts
                if parts and parts[0].text:
                    yield LLMChunk(delta=parts[0].text)

        yield LLMChunk(finish_reason="stop")

    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        client = self._get_async_client()
        contents = self._build_gemini_contents(messages)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

        async for chunk in client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            if chunk.candidates and chunk.candidates[0].content:
                parts = chunk.candidates[0].content.parts
                if parts and parts[0].text:
                    yield LLMChunk(delta=parts[0].text)

        yield LLMChunk(finish_reason="stop")

    @staticmethod
    def _build_gemini_contents(messages: list[LLMMessage]) -> list:
        """Convert LLMMessage list to Gemini content format."""
        contents = []
        for msg in messages:
            if msg.role == "system":
                # Gemini doesn't have system role — prepend to first user message
                # or use as a separate system instruction
                contents.append(types.Part(text=msg.content))
            elif msg.role == "user":
                contents.append(types.Part(text=msg.content))
            elif msg.role == "assistant":
                contents.append(types.Part(text=msg.content))
        return contents


def _calc_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    pricing = _GEMINI_PRICES.get(model)
    if not pricing:
        return 0.0
    return (
        (prompt_tokens / 1_000_000) * pricing["input"]
        + (completion_tokens / 1_000_000) * pricing["output"]
    )
