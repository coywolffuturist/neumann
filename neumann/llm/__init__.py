"""LLM Adapter — unified interface to multiple LLM providers.

Architecture:
- Base LLMAdapter defines the interface
- Each provider (OpenAI, Anthropic, Ollama) implements it
- LLMRouter auto-selects the best provider for a task
- Streaming supported for all providers
- Token counting & cost tracking built in

Supported providers:
- OpenAI (GPT-4, GPT-4o, o-series)
- Anthropic (Claude 3/4)
- Ollama (local LLMs — llama, qwen, codestral, etc.)

Usage:
    from neumann.llm import LLMRouter, LLMConfig
    router = LLMRouter()
    response = router.chat("Explain quantum computing", model="gpt-4o")
    
    # Streaming
    for chunk in router.stream("Write a Python function", model="claude-sonnet-4"):
        print(chunk.delta, end="")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


# ── Core types ───────────────────────────────────────────────────────

class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    GEMINI = "gemini"


@dataclass
class LLMMessage:
    """A single message in a conversation."""
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMUsage:
    """Token usage and cost tracking."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class LLMChunk:
    """A single chunk from a streaming response."""
    delta: str = ""
    finish_reason: str | None = None
    usage: LLMUsage | None = None


@dataclass
class LLMResponse:
    """Complete response from an LLM call."""
    content: str = ""
    provider: str = ""
    model: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

# MLX provider — available when mlx-lm is installed
MLX = "mlx"

