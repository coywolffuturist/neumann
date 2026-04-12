"""Tests for AsyncStreamingController."""
import pytest
import anyio
from neumann import AsyncStreamingController, TokenType, RenderContext


async def collect_async(chunks: list[str], env=None) -> list:
    ctrl = AsyncStreamingController(env=env or {})
    results = []
    for chunk in chunks:
        async for result in ctrl.feed(chunk):
            results.append(result)
    async for result in ctrl.flush():
        results.append(result)
    return results


@pytest.mark.anyio
async def test_plain_text():
    results = await collect_async(["Hello, world.\n"])
    assert any("Hello" in r.rendered for r in results)


@pytest.mark.anyio
async def test_code_block_split():
    chunks = ["```py", "thon\n", "print('hi')\n", "```\n"]
    results = await collect_async(chunks)
    assert len(results) == 1
    assert results[0].token.type == TokenType.CODE_BLOCK


@pytest.mark.anyio
async def test_tool_call_split():
    chunks = ['{"tool": "bash",', ' "input": {"command": "ls"}}\n']
    results = await collect_async(chunks)
    assert len(results) == 1
    assert results[0].token.type == TokenType.TOOL_CALL


@pytest.mark.anyio
async def test_flush_remaining():
    ctrl = AsyncStreamingController()
    results = []
    async for r in ctrl.feed("No newline at end"):
        results.append(r)
    async for r in ctrl.flush():
        results.append(r)
    assert len(results) == 1
    assert "No newline" in results[0].rendered


@pytest.mark.anyio
async def test_flush_empty():
    ctrl = AsyncStreamingController()
    results = [r async for r in ctrl.flush()]
    assert results == []


@pytest.mark.anyio
async def test_mixed_stream():
    chunks = [
        "Here is some code:\n\n",
        "```python\n",
        "def hello():\n    return 'world'\n",
        "```\n",
        "\nDone.\n",
    ]
    results = await collect_async(chunks)
    types = [r.token.type for r in results]
    assert TokenType.CODE_BLOCK in types


@pytest.mark.anyio
async def test_context_propagated():
    results = await collect_async(["```python\npass\n```\n"], env={"is_web": True})
    assert results[0].context == RenderContext.WEB_HTML
    assert "<pre>" in results[0].rendered


@pytest.mark.anyio
async def test_stats():
    ctrl = AsyncStreamingController()
    async for _ in ctrl.feed("chunk1\n"):
        pass
    async for _ in ctrl.feed("chunk2\n"):
        pass
    async for _ in ctrl.flush():
        pass
    assert ctrl.stats["chunks_in"] == 2
    assert ctrl.stats["tokens_out"] >= 1


@pytest.mark.anyio
async def test_max_buffer_force_flush():
    ctrl = AsyncStreamingController(max_buffer=20)
    results = [r async for r in ctrl.feed("x" * 25)]
    assert len(results) == 1
