"""Base LLM adapter — abstract interface for all providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Generator, AsyncGenerator
from typing import Any

from . import LLMMessage, LLMResponse, LLMChunk, LLMProvider


class LLMAdapter(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def provider(self) -> LLMProvider:
        """Which provider this adapter talks to."""
        ...

    @abstractmethod
    def available_models(self) -> list[str]:
        """List models supported by this provider."""
        ...

    @abstractmethod
    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request and get a complete response."""
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        """Stream the response chunk by chunk."""
        ...

    @abstractmethod
    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Async stream the response."""
        ...

    def chat_simple(
        self,
        prompt: str,
        system_prompt: str = "You are a helpful coding assistant.",
        model: str = "default",
        **kwargs: Any,
    ) -> str:
        """Simple convenience: send prompt, return text."""
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=prompt),
        ]
        response = self.chat(messages, model=model, **kwargs)
        return response.content
