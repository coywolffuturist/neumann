"""Tests for NeumannPipeline end-to-end."""
from neumann import NeumannPipeline, TokenType, RenderContext


def test_pipeline_code_block():
    pipeline = NeumannPipeline()
    result = pipeline.process("```python\nprint('hi')\n```", env={"is_api": True})
    assert result.token.type == TokenType.CODE_BLOCK
    assert result.context == RenderContext.API_JSON
    assert result.decision.formatter == "CodeBlockRenderer"
    assert result.validation.valid
    assert result.duration_ms >= 0
    assert result.input_hash.startswith("sha256:")


def test_pipeline_tool_call():
    pipeline = NeumannPipeline()
    result = pipeline.process('{"tool": "bash", "input": {}}')
    assert result.token.type == TokenType.TOOL_CALL
    assert result.decision.formatter == "ToolCallRenderer"


def test_pipeline_with_validation_failure():
    pipeline = NeumannPipeline(output_schema={"forbidden": ["secret_key"]})
    result = pipeline.process("Here is your secret_key: abc123")
    assert not result.validation.valid
    assert result.validation.severity == "fatal"
