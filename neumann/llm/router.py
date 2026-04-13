"""LLM Router — auto-selects the best provider for a task, or uses explicit model.

Usage:
    from neumann.llm import LLMRouter, LLMConfig
    
    # Auto mode — picks best available provider
    router = LLMRouter()
    response = router.chat("Write a Python function to sort a list")
    
    # Explicit model
    response = router.chat("Explain async/await", model="gpt-4o")
    
    # Streaming
    for chunk in router.stream("Write a REST API"):
        print(chunk.delta, end="")
"""
from __future__ import annotations

from collections.abc import Generator, AsyncGenerator
from dataclasses import dataclass
from typing import Any

from .adapter import LLMAdapter
from .openai_adapter import OpenAIAdapter
from .anthropic_adapter import AnthropicAdapter
from .ollama_adapter import OllamaAdapter
from . import LLMMessage, LLMResponse, LLMChunk, LLMProvider


@dataclass
class LLMConfig:
    """Configuration for the LLM router."""
    # API keys (can also be set via env vars: OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY)
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None

    # Base URLs
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Default model per provider
    default_openai_model: str = "gpt-4o"
    default_anthropic_model: str = "claude-sonnet-4-20250514"
    default_ollama_model: str = "qwen2.5-coder"
    default_gemini_model: str = "gemini-2.0-flash"

    # Timeout
    timeout: int = 120

    # Default system prompt
    system_prompt: str = (
        "You are a skilled coding assistant. "
        "Write clean, correct, and well-documented code. "
        "Explain your reasoning concisely."
    )


class LLMRouter:
    """Routes LLM requests to the best available provider."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        adapters: dict[str, LLMAdapter] | None = None,
    ) -> None:
        self.config = config or LLMConfig()
        self._adapters: dict[str, LLMAdapter] = adapters or {}
        self._default_model: str | None = None

        # Register default adapters if none provided
        if not adapters:
            self._register_default_adapters()

    # ── registration ──────────────────────────────────────────────────────

    def register(self, name: str, adapter: LLMAdapter) -> None:
        """Register a custom adapter."""
        self._adapters[name] = adapter

    def unregister(self, name: str) -> None:
        """Remove an adapter."""
        self._adapters.pop(name, None)

    def list_adapters(self) -> list[str]:
        """List available adapter names."""
        return list(self._adapters.keys())

    def get_adapter(self, name: str) -> LLMAdapter | None:
        """Get an adapter by name."""
        return self._adapters.get(name)

    def set_default_model(self, model: str) -> None:
        """Set the default model for auto-routing."""
        self._default_model = model

    # ── chat ──────────────────────────────────────────────────────────────

    def chat(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        history: list[LLMMessage] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat request to the best available provider.
        
        If model is specified, it will try to find the right adapter.
        Otherwise, it picks the first available adapter.
        """
        messages = self._build_messages(prompt, system_prompt, history)
        target_model = model or self._default_model or self.config.default_openai_model

        adapter, resolved_model = self._resolve_adapter(target_model)
        if adapter is None:
            raise RuntimeError(
                f"No LLM adapter available. "
                f"Install a provider: pip install openai anthropic, or start Ollama."
            )

        return adapter.chat(
            messages=messages,
            model=resolved_model,
            **kwargs,
        )

    def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        history: list[LLMMessage] | None = None,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        """Stream a response chunk by chunk."""
        messages = self._build_messages(prompt, system_prompt, history)
        target_model = model or self._default_model or self.config.default_openai_model

        adapter, resolved_model = self._resolve_adapter(target_model)
        if adapter is None:
            raise RuntimeError("No LLM adapter available.")

        yield from adapter.stream(messages=messages, model=resolved_model, **kwargs)

    async def astream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        history: list[LLMMessage] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Async stream a response."""
        messages = self._build_messages(prompt, system_prompt, history)
        target_model = model or self._default_model or self.config.default_openai_model

        adapter, resolved_model = self._resolve_adapter(target_model)
        if adapter is None:
            raise RuntimeError("No LLM adapter available.")

        async for chunk in adapter.astream(
            messages=messages, model=resolved_model, **kwargs
        ):
            yield chunk

    # ── private ───────────────────────────────────────────────────────────

    def _register_default_adapters(self) -> None:
        cfg = self.config

        # Try OpenAI
        import os
        openai_key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if openai_key:
            try:
                self._adapters["openai"] = OpenAIAdapter(
                    api_key=openai_key,
                    base_url=cfg.openai_base_url,
                    timeout=cfg.timeout,
                )
            except ImportError:
                pass

        # Try Anthropic
        anthropic_key = cfg.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                self._adapters["anthropic"] = AnthropicAdapter(
                    api_key=anthropic_key,
                    base_url=cfg.anthropic_base_url,
                    timeout=cfg.timeout,
                )
            except ImportError:
                pass

        # Try Ollama (always register — no API key needed)
        try:
            ollama = OllamaAdapter(base_url=cfg.ollama_base_url, timeout=cfg.timeout)
            # Quick connectivity check
            self._check_ollama(ollama)
            self._adapters["ollama"] = ollama
        except (ConnectionError, ImportError, OSError):
            pass

        # Try Gemini (free tier available)
        import os
        gemini_key = cfg.gemini_api_key if hasattr(cfg, 'gemini_api_key') else os.environ.get("GEMINI_API_KEY")
        if gemini_key:
            try:
                from .gemini_adapter import GeminiAdapter
                self._adapters["gemini"] = GeminiAdapter(
                    api_key=gemini_key,
                    timeout=cfg.timeout,
                )
            except ImportError:
                pass

    @staticmethod
    def _check_ollama(adapter: OllamaAdapter) -> None:
        """Quick check if Ollama is reachable."""
        import urllib.request
        import urllib.error
        try:
            urllib.request.urlopen(
                f"{adapter.base_url}/api/tags", timeout=5
            )
        except Exception as e:
            raise ConnectionError(f"Ollama not reachable: {e}")

    def _resolve_adapter(self, model: str) -> tuple[LLMAdapter | None, str]:
        """Find the right adapter for a model name."""
        model_lower = model.lower()

        # Gemini models
        if "gemini" in model_lower:
            adapter = self._adapters.get("gemini")
            if adapter:
                return adapter, model
            return None, model

        # OpenAI models
        if any(m in model_lower for m in ("gpt-", "o1", "o3")):
            adapter = self._adapters.get("openai")
            if adapter:
                return adapter, model
            return None, model

        # Anthropic models
        if "claude" in model_lower:
            adapter = self._adapters.get("anthropic")
            if adapter:
                return adapter, model
            return None, model

        # Ollama models
        if adapter := self._adapters.get("ollama"):
            return adapter, model

        # Fallback: try any available adapter
        for name, a in self._adapters.items():
            return a, model

        return None, model

    @staticmethod
    def _build_messages(
        prompt: str,
        system_prompt: str | None,
        history: list[LLMMessage] | None,
    ) -> list[LLMMessage]:
        messages: list[LLMMessage] = []
        if history:
            messages.extend(history)
        messages.append(LLMMessage(role="user", content=prompt))
        # Insert system prompt at the beginning if not already present
        has_system = any(m.role == "system" for m in messages)
        if not has_system and system_prompt:
            messages.insert(0, LLMMessage(role="system", content=system_prompt))
        return messages
