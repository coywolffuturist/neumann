"""Ollama adapter — local LLMs (Llama, Qwen, Codestral, etc.).

Uses the Ollama REST API (default: http://localhost:11434).
No API key required — runs entirely locally.

Usage:
    from neumann.llm.ollama_adapter import OllamaAdapter
    llm = OllamaAdapter(base_url="http://localhost:11434")
    response = llm.chat_simple("Write hello world in Python", model="qwen2.5-coder")
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from collections.abc import Generator, AsyncGenerator
from typing import Any

from .adapter import LLMAdapter
from . import LLMProvider, LLMMessage, LLMResponse, LLMChunk, LLMUsage

# Common local models
_OLLAMA_MODELS = [
    "qwen2.5-coder", "qwen2.5-coder:32b",
    "codestral", "codestral:22b",
    "llama3.1", "llama3.1:70b", "llama3.2",
    "deepseek-coder", "deepseek-coder-v2",
    "mistral", "mixtral",
    "phi3", "phi4",
    "starcoder2",
]


class OllamaAdapter(LLMAdapter):
    """LLM adapter for Ollama — local, no API key needed."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.OLLAMA

    def available_models(self) -> list[str]:
        return list(_OLLAMA_MODELS)

    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "qwen2.5-coder",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        ollama_msgs = [{"role": m.role, "content": m.content} for m in messages]

        payload = {
            "model": model,
            "messages": ollama_msgs,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        payload["options"].update(kwargs)

        resp_data = self._post("/api/chat", payload)

        message = resp_data.get("message", {})
        content = message.get("content", "")

        # Ollama may not provide token counts in non-streaming mode
        usage = LLMUsage(
            prompt_tokens=resp_data.get("prompt_eval_count", 0),
            completion_tokens=resp_data.get("eval_count", 0),
            total_tokens=(
                resp_data.get("prompt_eval_count", 0)
                + resp_data.get("eval_count", 0)
            ),
            # Local models are free — cost is electricity + hardware
            estimated_cost_usd=0.0,
        )

        return LLMResponse(
            content=content,
            provider=self.provider.value,
            model=model,
            usage=usage,
            finish_reason="stop" if resp_data.get("done") else None,
            metadata={"total_duration_ms": resp_data.get("total_duration", 0)},
        )

    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "qwen2.5-coder",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        ollama_msgs = [{"role": m.role, "content": m.content} for m in messages]

        payload = {
            "model": model,
            "messages": ollama_msgs,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        payload["options"].update(kwargs)

        yield from self._stream_post("/api/chat", payload)

    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "qwen2.5-coder",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        # Use sync streaming in async context for simplicity
        # In production, use aiohttp or httpx
        for chunk in self.stream(messages, model, temperature, max_tokens, **kwargs):
            yield chunk

    # ── HTTP helpers ──────────────────────────────────────────────────────

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot reach Ollama at {self.base_url}: {e}"
            ) from e

    def _stream_post(
        self, endpoint: str, payload: dict
    ) -> Generator[LLMChunk, None, None]:
        url = f"{self.base_url}{endpoint}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                buffer = b""
                for chunk in resp.read(4096):
                    buffer += chunk
                    # Process complete JSON objects from buffer
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            message = obj.get("message", {})
                            content = message.get("content", "")
                            if content:
                                yield LLMChunk(delta=content)
                            if obj.get("done"):
                                yield LLMChunk(
                                    finish_reason="stop",
                                    usage=LLMUsage(
                                        prompt_tokens=obj.get("prompt_eval_count", 0),
                                        completion_tokens=obj.get("eval_count", 0),
                                        total_tokens=(
                                            obj.get("prompt_eval_count", 0)
                                            + obj.get("eval_count", 0)
                                        ),
                                    ),
                                )
                        except json.JSONDecodeError:
                            continue
        except urllib.error.URLError as e:
            raise ConnectionError(
                f"Cannot reach Ollama at {self.base_url}: {e}"
            ) from e
