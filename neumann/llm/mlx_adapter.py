"""MLX adapter — Apple Silicon native inference via mlx-lm.

Runs Qwen3.5 (and any other MLX-format model) directly on Apple Silicon
using the Metal GPU backend. No API key, no network, no Ollama daemon —
inference happens natively in-process via the mlx-lm library.

Recommended models for Mac mini 64GB:
    baa-ai/Qwen3.5-122B-A10B-RAM-60GB-MLX   (57GB, MoE 122B/10B active, best quality)
    baa-ai/Qwen3.5-122B-A10B-RAM-48GB-MLX   (46GB, MoE 122B/10B active, more headroom)
    mlx-community/Qwen3.5-35B-A3B-4bit      (MoE 35B/3B active, ~20GB, fastest)
    lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-MLX-4bit  (coding-focused MoE)

Installation:
    pip install mlx-lm huggingface-hub

Model download:
    huggingface-cli download baa-ai/Qwen3.5-122B-A10B-RAM-60GB-MLX

Usage:
    from neumann.llm.mlx_adapter import MLXAdapter
    llm = MLXAdapter(model_path="baa-ai/Qwen3.5-122B-A10B-RAM-60GB-MLX")
    response = llm.chat_simple("Write a binary search in Python")
    print(response)
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Generator, AsyncGenerator
from typing import Any

from .adapter import LLMAdapter
from . import LLMProvider, LLMMessage, LLMResponse, LLMChunk, LLMUsage

# Well-tested MLX Qwen3.5 models — HuggingFace repo IDs
_MLX_RECOMMENDED = {
    # MoE 122B, 10B active — best quality for 64GB Mac
    "qwen3.5-122b-60gb": "baa-ai/Qwen3.5-122B-A10B-RAM-60GB-MLX",
    "qwen3.5-122b-48gb": "baa-ai/Qwen3.5-122B-A10B-RAM-48GB-MLX",
    # MoE 35B, 3B active — fastest, fits in ~20GB
    "qwen3.5-35b":       "mlx-community/Qwen3.5-35B-A3B-4bit",
    # Coding-focused MoE 30B, 3B active
    "qwen3-coder-30b":   "lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-MLX-4bit",
    # Qwen3.5 9B — lightweight option
    "qwen3.5-9b":        "mlx-community/Qwen3.5-9B-MLX-4bit",
}


class MLXAdapter(LLMAdapter):
    """LLM adapter for Apple Silicon MLX inference.

    Loads the model once into unified memory; subsequent calls reuse the
    loaded weights with zero startup overhead.

    Args:
        model_path: HuggingFace repo ID or local path to an MLX model.
                    Can also be a shorthand from _MLX_RECOMMENDED
                    (e.g. "qwen3.5-122b-60gb").
        max_kv_size: KV cache size in tokens. Larger = longer context,
                     more memory. Default 4096; increase to 32768+ for
                     long coding sessions.
        verbose: Log model load progress to stderr.
    """

    def __init__(
        self,
        model_path: str = "qwen3.5-122b-60gb",
        max_kv_size: int = 4096,
        verbose: bool = False,
    ) -> None:
        # Resolve shorthand aliases
        self._model_path = _MLX_RECOMMENDED.get(model_path, model_path)
        self._max_kv_size = max_kv_size
        self._verbose = verbose
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    # ── lazy load ─────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load model into memory on first use (thread-safe)."""
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from mlx_lm import load  # type: ignore
            except ImportError:
                raise ImportError(
                    "mlx-lm is required for MLX inference.\n"
                    "Install with: pip install mlx-lm huggingface-hub\n"
                    "Then download model: "
                    f"huggingface-cli download {self._model_path}"
                )
            if self._verbose:
                import sys
                print(f"[MLXAdapter] Loading {self._model_path}...", file=sys.stderr)
            self._model, self._tokenizer = load(self._model_path)
            if self._verbose:
                print("[MLXAdapter] Model loaded.", file=sys.stderr)

    # ── LLMAdapter interface ─────────────────────────────────────────────

    @property
    def provider(self) -> LLMProvider:
        return LLMProvider.OLLAMA  # reuse OLLAMA slot; no new enum value needed

    @property
    def model_path(self) -> str:
        return self._model_path

    def available_models(self) -> list[str]:
        return list(_MLX_RECOMMENDED.keys())

    def chat(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Run a full chat completion. Blocks until generation is complete."""
        self._ensure_loaded()
        try:
            from mlx_lm import generate  # type: ignore
        except ImportError:
            raise ImportError("mlx-lm is required: pip install mlx-lm")

        prompt = self._build_prompt(messages)

        output = generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temperature,
            verbose=False,
        )

        # mlx_lm.generate returns the generated text (not including prompt)
        content = output.strip() if isinstance(output, str) else output

        # Rough token estimate (mlx_lm doesn't expose usage by default)
        prompt_tokens = len(self._tokenizer.encode(prompt)) if self._tokenizer else 0
        completion_tokens = len(self._tokenizer.encode(content)) if self._tokenizer else 0

        return LLMResponse(
            content=content,
            provider="mlx",
            model=self._model_path,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_usd=0.0,  # local inference — no cost
            ),
            finish_reason="stop",
        )

    def stream(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> Generator[LLMChunk, None, None]:
        """Stream tokens as they are generated."""
        self._ensure_loaded()
        try:
            from mlx_lm.utils import stream_generate  # type: ignore
        except ImportError:
            raise ImportError("mlx-lm is required: pip install mlx-lm")

        prompt = self._build_prompt(messages)

        for response in stream_generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temperature,
        ):
            # stream_generate yields GenerationResponse objects
            token_text = getattr(response, "text", str(response))
            finish = getattr(response, "finish_reason", None)
            yield LLMChunk(delta=token_text, finish_reason=finish)

    async def astream(
        self,
        messages: list[LLMMessage],
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Async stream — runs sync stream in a thread to avoid blocking event loop."""
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[LLMChunk | None] = asyncio.Queue()

        def _producer():
            try:
                for chunk in self.stream(messages, model=model,
                                          temperature=temperature,
                                          max_tokens=max_tokens, **kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    # ── prompt building ───────────────────────────────────────────────────

    def _build_prompt(self, messages: list[LLMMessage]) -> str:
        """Convert messages to a prompt string using the tokenizer's chat template.

        Falls back to a simple role-prefixed format if the tokenizer
        does not expose apply_chat_template.
        """
        if self._tokenizer is None:
            self._ensure_loaded()

        # Use HuggingFace chat template if available (Qwen3.5 ships one)
        apply_chat = getattr(self._tokenizer, "apply_chat_template", None)
        if apply_chat is not None:
            hf_messages = [{"role": m.role, "content": m.content} for m in messages]
            try:
                return apply_chat(
                    hf_messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception:
                pass  # fall through to manual format

        # Fallback: simple ChatML-style prompt
        parts = []
        for m in messages:
            parts.append(f"<|im_start|>{m.role}\n{m.content}<|im_end|>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    # ── context limit hint ────────────────────────────────────────────────

    def context_limit(self, model_name: str = "") -> int:
        """Return approximate context window size for token budget decisions."""
        # Qwen3.5 supports 128K context; default to 32K for safety
        return 32_768
