"""Anthropic adapter — Claude 3/4 models.

Uses the official anthropic SDK (optional — falls back to HTTP if not installed).

Usage:
    from neumann.llm.anthropic_adapter import AnthropicAdapter
    llm = AnthropicAdapter(api_key="sk-ant-...")
    response = llm.chat_simple("Write hello world in Python", model="claude-sonnet-4-20250514")
"""
from __future__ import annotations

from collections.abc import Generator, AsyncGenerator
from typing import Any

try:
    from anthropic import Anthropic, AsyncAnthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

from .adapter import LLMAdapter
from . import LLMProvider, LLMMessage, LLMResponse, LLMChunk, LLMUsage

_ANTHROPIC_MODELS = [
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
]

# Pricing per 1M tokens (as of 2025)
_ANTHROPIC_PRICES = {
    "claude-opus-4-20250514":    {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-20250514":  {"input": 3.00,  "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80,  "output": 4.00},
    "claude-3-opus-20240229":    {"input": 15.00, "output": 75.00},
}


class AnthropicAdapter(LLMAdapter):
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
        return LLMProvider.ANTHROPIC

    def available_models(self) -> list[str]:
        return list(_ANTHROPIC_MODELS)

    def _get_client(self) -> Any:
        if self._client is None:
            if not _HAS_ANTHROPIC:
                raise ImportError(
                    "anthropic package required. Install: pip install anthropic"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Anthropic(**kwargs)
        return self._client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            if not _HAS_ANTHROPIC:
                raise ImportError(
                    "anthropic package required. Install: pip install anthropic"
                )
            kwargs = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._async_client = AsyncAnthropic(**kwargs)
        return self._async_client

    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        # Anthropic uses different message format
        system_msg = ""
        user_msgs = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_msgs.append({"role": m.role, "content": m.content})

        resp = client.messages.create(
            model=model,
            system=system_msg,
            messages=user_msgs,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

        content = resp.content[0].text if resp.content else ""
        usage = LLMUsage(
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens,
            total_tokens=resp.usage.input_tokens + resp.usage.output_tokens,
            estimated_cost_usd=_calc_cost(
                resp.usage.input_tokens,
                resp.usage.output_tokens,
                model,
            ),
        )

        return LLMResponse(
            content=content,
            provider=self.provider.value,
            model=model,
            usage=usage,
            finish_reason=resp.stop_reason,
        )

    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        client = self._get_client()

        system_msg = ""
        user_msgs = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_msgs.append({"role": m.role, "content": m.content})

        with client.messages.stream(
            model=model,
            system=system_msg,
            messages=user_msgs,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ) as stream:
            for text in stream.text_stream:
                yield LLMChunk(delta=text)
            yield LLMChunk(finish_reason="end_turn")

    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        client = self._get_async_client()

        system_msg = ""
        user_msgs = []
        for m in messages:
            if m.role == "system":
                system_msg = m.content
            else:
                user_msgs.append({"role": m.role, "content": m.content})

        async with client.messages.stream(
            model=model,
            system=system_msg,
            messages=user_msgs,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        ) as stream:
            async for text in stream.text_stream:
                yield LLMChunk(delta=text)
            yield LLMChunk(finish_reason="end_turn")


def _calc_cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    pricing = _ANTHROPIC_PRICES.get(model)
    if not pricing:
        return 0.0
    return (
        (prompt_tokens / 1_000_000) * pricing["input"]
        + (completion_tokens / 1_000_000) * pricing["output"]
    )
