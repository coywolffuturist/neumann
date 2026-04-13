"""OpenAI adapter — GPT-4, GPT-4o, o-series models.

Uses the official openai SDK (optional — falls back to HTTP if not installed).

Usage:
    from neumann.llm.openai_adapter import OpenAIAdapter
    llm = OpenAIAdapter(api_key="sk-...")
    response = llm.chat_simple("Write hello world in Python", model="gpt-4o")
"""
from __future__ import annotations

from collections.abc import Generator, AsyncGenerator
from typing import Any
import json
import time

try:
    from openai import OpenAI, AsyncOpenAI
    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

from .adapter import LLMAdapter
from . import LLMProvider, LLMMessage, LLMResponse, LLMChunk, LLMUsage

_OPENAI_MODELS = [
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
    "o1", "o1-mini", "o3-mini",
]

# Rough pricing per 1M tokens (as of 2025)
_OPENAI_PRICES = {
    "gpt-4o":       {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":  {"input": 0.15, "output": 0.60},
    "gpt-4-turbo":  {"input": 10.0, "output": 30.00},
    "gpt-4":        {"input": 30.0, "output": 60.00},
    "o1":           {"input": 15.0, "output": 60.00},
    "o1-mini":      {"input": 1.10, "output": 4.40},
    "o3-mini":      {"input": 1.10, "output": 4.40},
}


class OpenAIAdapter(LLMAdapter):
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
        return LLMProvider.OPENAI

    def available_models(self) -> list[str]:
        return list(_OPENAI_MODELS)

    def _get_client(self) -> Any:
        if self._client is None:
            if not _HAS_OPENAI:
                raise ImportError(
                    "openai package required. Install: pip install openai"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            if not _HAS_OPENAI:
                raise ImportError(
                    "openai package required. Install: pip install openai"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()
        openai_msgs = [{"role": m.role, "content": m.content} for m in messages]

        chat_kwargs = {
            "model": model,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
        }
        # o-series models don't support temperature
        if not model.startswith(("o1", "o3")):
            chat_kwargs["temperature"] = temperature
        chat_kwargs.update(kwargs)

        resp = client.chat.completions.create(**chat_kwargs)
        choice = resp.choices[0]

        usage = LLMUsage(
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
            total_tokens=resp.usage.total_tokens if resp.usage else 0,
            estimated_cost_usd=_calc_cost(
                resp.usage.prompt_tokens if resp.usage else 0,
                resp.usage.completion_tokens if resp.usage else 0,
                model,
            ),
        )

        return LLMResponse(
            content=choice.message.content or "",
            provider=self.provider.value,
            model=model,
            usage=usage,
            finish_reason=choice.finish_reason,
        )

    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        client = self._get_client()
        openai_msgs = [{"role": m.role, "content": m.content} for m in messages]

        chat_kwargs = {
            "model": model,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if not model.startswith(("o1", "o3")):
            chat_kwargs["temperature"] = temperature
        chat_kwargs.update(kwargs)

        for chunk in client.chat.completions.create(**chat_kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield LLMChunk(delta=delta.content)
            if chunk.choices and chunk.choices[0].finish_reason:
                yield LLMChunk(finish_reason=chunk.choices[0].finish_reason)

    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        client = self._get_async_client()
        openai_msgs = [{"role": m.role, "content": m.content} for m in messages]

        chat_kwargs = {
            "model": model,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if not model.startswith(("o1", "o3")):
            chat_kwargs["temperature"] = temperature
        chat_kwargs.update(kwargs)

        async for chunk in await client.chat.completions.create(**chat_kwargs):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield LLMChunk(delta=delta.content)
            if chunk.choices and chunk.choices[0].finish_reason:
                yield LLMChunk(finish_reason=chunk.choices[0].finish_reason)


def _calc_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    pricing = _OPENAI_PRICES.get(model)
    if not pricing:
        return 0.0
    return (
        (prompt_tokens / 1_000_000) * pricing["input"]
        + (completion_tokens / 1_000_000) * pricing["output"]
    )
