"""StreamingController — buffers a chunked LLM stream and flushes complete tokens.

Handles the hard problem of streaming: LLM output arrives in arbitrary chunks.
A code fence might arrive as "```py" in one chunk and "thon\\nprint" in the next.
The controller accumulates chunks, detects token boundaries, and yields
complete tokens to the pipeline only when they're fully formed.

Usage:
    pipeline = NeumannPipeline()
    controller = StreamingController(pipeline, env={"is_api": True})

    for chunk in llm_stream:
        for result in controller.feed(chunk):
            print(result.rendered)

    for result in controller.flush():
        print(result.rendered)
"""
from __future__ import annotations

import re
from collections.abc import Generator
from typing import Any

from .pipeline import NeumannPipeline, PipelineResult
from .types import RenderContext


# ── Boundary detection ────────────────────────────────────────────────────────
#
# We look for "hard" boundaries — points where we know a token is complete.
# Order matters: more specific patterns are checked first.

_BOUNDARIES: list[tuple[str, re.Pattern]] = [
    # Closing code fence on its own line
    ("code_fence_close",  re.compile(r'```\s*\n')),
    # Tool call / result: complete JSON object followed by newline
    ("json_object",       re.compile(r'^\s*\{[^{}]*\}\s*\n', re.MULTILINE | re.DOTALL)),
    # Diff hunk header
    ("diff_hunk",         re.compile(r'@@ .+ @@.*\n(?:[+\- ].*\n)*')),
    # Paragraph break (double newline)
    ("paragraph",         re.compile(r'\n\n+')),
    # Single newline (weakest boundary — only used for plain text flush)
    ("newline",           re.compile(r'\n')),
]

# Tokens that need to stay open until their closing marker
_OPEN_MARKERS = {
    "code_fence": re.compile(r'^```'),
}


class StreamingController:
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

    def feed(self, chunk: str) -> Generator[PipelineResult, None, None]:
        """Feed a raw chunk from the LLM stream. Yields complete PipelineResults."""
        self._stats["chunks_in"] += 1
        self._stats["bytes_in"] += len(chunk)
        self._buf += chunk

        # Safety valve — if buffer grows huge, force a flush
        if len(self._buf) > self.max_buffer:
            yield from self._force_flush()
            return

        yield from self._drain()

    def flush(self) -> Generator[PipelineResult, None, None]:
        """Flush any remaining buffered content. Call after the stream ends."""
        if self._buf.strip():
            yield self.pipeline.process(self._buf, self.env)
            self._buf = ""
            self._in_code_fence = False
            self._stats["tokens_out"] += 1

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ── private ───────────────────────────────────────────────────────────────

    def _drain(self) -> Generator[PipelineResult, None, None]:
        """Try to extract and emit complete tokens from the buffer."""
        while self._buf:
            # Track code fence open/close state
            if not self._in_code_fence:
                fence_match = _OPEN_MARKERS["code_fence"].match(self._buf)
                if fence_match:
                    self._in_code_fence = True

            if self._in_code_fence:
                # Wait for closing fence
                close = re.search(r'\n```[ \t]*\n?', self._buf)
                if close:
                    end = close.end()
                    token_text = self._buf[:end]
                    self._buf = self._buf[end:]
                    self._in_code_fence = False
                    yield self._emit(token_text)
                else:
                    break  # incomplete fence — wait for more chunks
            else:
                # Try each boundary in priority order
                emitted = False
                for name, pattern in _BOUNDARIES:
                    if name in ("code_fence_close",):
                        continue  # handled above
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
                    break  # nothing to flush yet

    def _emit(self, text: str) -> PipelineResult:
        self._stats["tokens_out"] += 1
        return self.pipeline.process(text, self.env)

    def _force_flush(self) -> Generator[PipelineResult, None, None]:
        """Emergency flush when buffer exceeds max_buffer."""
        yield self._emit(self._buf)
        self._buf = ""
        self._in_code_fence = False
