"""AsyncStreamingController — async version of StreamingController.

Identical logic to StreamingController but uses async generators,
compatible with FastAPI, asyncio, aiohttp, websockets, and all
async LLM clients (OpenAI, Anthropic, LiteLLM, LangChain, etc.).

Usage:
    ctrl = AsyncStreamingController(env={"is_api": True})

    async for chunk in llm.stream(...):
        async for result in ctrl.feed(chunk):
            await websocket.send(result.rendered)

    async for result in ctrl.flush():
        await websocket.send(result.rendered)
"""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

from .pipeline import NeumannPipeline, PipelineResult
from .streaming import _BOUNDARIES, _OPEN_MARKERS  # reuse boundary patterns


class AsyncStreamingController:
    def __init__(
        self,
        pipeline: NeumannPipeline | None = None,
        env: dict[str, Any] | None = None,
        max_buffer: int = 32_768,
    ) -> None:
        self.pipeline = pipeline or NeumannPipeline()
        self.env = env or {}
        self.max_buffer = max_buffer

        self._buf = ""
        self._in_code_fence = False
        self._stats = {"chunks_in": 0, "tokens_out": 0, "bytes_in": 0}

    # ── public ────────────────────────────────────────────────────────────────

    async def feed(self, chunk: str) -> AsyncGenerator[PipelineResult, None]:
        """Feed a raw chunk. Yields complete PipelineResults asynchronously."""
        self._stats["chunks_in"] += 1
        self._stats["bytes_in"] += len(chunk)
        self._buf += chunk

        if len(self._buf) > self.max_buffer:
            async for result in self._force_flush():
                yield result
            return

        async for result in self._drain():
            yield result

    async def flush(self) -> AsyncGenerator[PipelineResult, None]:
        """Flush remaining buffer after the stream ends."""
        if self._buf.strip():
            yield self._emit(self._buf)
            self._buf = ""
            self._in_code_fence = False
            self._stats["tokens_out"] += 1

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── private ───────────────────────────────────────────────────────────────

    async def _drain(self) -> AsyncGenerator[PipelineResult, None]:
        while self._buf:
            if not self._in_code_fence:
                if _OPEN_MARKERS["code_fence"].match(self._buf):
                    self._in_code_fence = True

            if self._in_code_fence:
                close = re.search(r'\n```[ \t]*\n?', self._buf)
                if close:
                    end = close.end()
                    token_text = self._buf[:end]
                    self._buf = self._buf[end:]
                    self._in_code_fence = False
                    yield self._emit(token_text)
                else:
                    break
            else:
                emitted = False
                for name, pattern in _BOUNDARIES:
                    if name == "code_fence_close":
                        continue
                    match = pattern.search(self._buf)
                    if match:
                        end = match.end()
                        token_text = self._buf[:end]
                        self._buf = self._buf[end:]
                        if token_text.strip():
                            yield self._emit(token_text)
                        emitted = True
                        break
                if not emitted:
                    break

    async def _force_flush(self) -> AsyncGenerator[PipelineResult, None]:
        yield self._emit(self._buf)
        self._buf = ""
        self._in_code_fence = False

    def _emit(self, text: str) -> PipelineResult:
        self._stats["tokens_out"] += 1
        return self.pipeline.process(text, self.env)
