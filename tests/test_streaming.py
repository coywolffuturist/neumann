"""Tests for StreamingController."""
import pytest
from neumann import StreamingController, NeumannPipeline, TokenType


def collect(chunks: list[str], env=None) -> list:
    """Helper: feed chunks through controller, collect all results including flush."""
    ctrl = StreamingController(env=env or {})
    results = []
    for chunk in chunks:
        results.extend(ctrl.feed(chunk))
    results.extend(ctrl.flush())
    return results


# ── Basic functionality ───────────────────────────────────────

def test_plain_text_single_chunk():
    results = collect(["Hello, world.\n"])
    assert len(results) >= 1
    assert any("Hello" in r.rendered for r in results)


def test_code_block_split_across_chunks():
    """Code fence split mid-open — controller must buffer until closing fence."""
    chunks = ["```py", "thon\n", "print('hi')\n", "```\n"]
    results = collect(chunks)
    assert len(results) == 1
    assert results[0].token.type == TokenType.CODE_BLOCK


def test_code_block_all_at_once():
    results = collect(["```python\nprint('hi')\n```\n"])
    assert len(results) == 1
    assert results[0].token.type == TokenType.CODE_BLOCK


def test_multiple_paragraphs():
    chunks = ["First paragraph.\n\nSecond paragraph.\n\n"]
    results = collect(chunks)
    assert len(results) == 2


def test_tool_call_split():
    chunks = ['{"tool": "bash",', ' "input": {"command": "ls"}}\n']
    results = collect(chunks)
    assert len(results) == 1
    assert results[0].token.type == TokenType.TOOL_CALL


def test_flush_emits_remaining():
    ctrl = StreamingController()
    list(ctrl.feed("Incomplete sentence without newline"))
    results = list(ctrl.flush())
    assert len(results) == 1
    assert "Incomplete" in results[0].rendered


def test_flush_empty_buffer_emits_nothing():
    ctrl = StreamingController()
    results = list(ctrl.flush())
    assert results == []


# ── Mixed content stream ──────────────────────────────────────

def test_mixed_stream():
    """Simulate a realistic LLM stream: text + code block + text."""
    chunks = [
        "Here is some code:\n\n",
        "```python\n",
        "def hello():\n",
        "    return 'world'\n",
        "```\n",
        "\nAnd that's it.\n",
    ]
    results = collect(chunks)
    types = [r.token.type for r in results]
    assert TokenType.CODE_BLOCK in types
    assert TokenType.PLAIN_TEXT in types or TokenType.MARKDOWN in types


# ── Stats ─────────────────────────────────────────────────────

def test_stats_tracked():
    ctrl = StreamingController()
    list(ctrl.feed("Hello\n"))
    list(ctrl.feed("World\n"))
    list(ctrl.flush())
    assert ctrl.stats["chunks_in"] == 2
    assert ctrl.stats["tokens_out"] >= 1
    assert ctrl.stats["bytes_in"] > 0


# ── Safety valve ──────────────────────────────────────────────

def test_max_buffer_force_flush():
    ctrl = StreamingController(max_buffer=20)
    big_chunk = "x" * 25  # exceeds max_buffer
    results = list(ctrl.feed(big_chunk))
    assert len(results) == 1  # force-flushed


# ── Context propagation ───────────────────────────────────────

def test_context_propagated_to_results():
    results = collect(["```python\npass\n```\n"], env={"is_web": True})
    assert len(results) == 1
    from neumann import RenderContext
    assert results[0].context == RenderContext.WEB_HTML
    assert "<pre>" in results[0].rendered
