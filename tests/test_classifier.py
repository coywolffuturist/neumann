"""Tests for TokenClassifier."""
import pytest
from neumann import TokenClassifier, TokenType


@pytest.fixture
def clf():
    return TokenClassifier()


def test_code_block(clf):
    t = clf.classify("```python\nprint('hello')\n```")
    assert t.type == TokenType.CODE_BLOCK
    assert t.metadata.get("language") == "python"


def test_code_block_no_lang(clf):
    t = clf.classify("```\nsome code\n```")
    assert t.type == TokenType.CODE_BLOCK


def test_inline_code(clf):
    t = clf.classify("`foo()`")
    assert t.type == TokenType.INLINE_CODE


def test_diff(clf):
    t = clf.classify("@@ -1,3 +1,4 @@\n context")
    assert t.type == TokenType.DIFF


def test_git_diff(clf):
    t = clf.classify("diff --git a/foo.py b/foo.py")
    assert t.type == TokenType.DIFF


def test_tool_call(clf):
    t = clf.classify('{"tool": "bash", "input": {"command": "ls"}}')
    assert t.type == TokenType.TOOL_CALL


def test_error(clf):
    t = clf.classify("TypeError: unsupported operand type(s)")
    assert t.type == TokenType.ERROR


def test_traceback(clf):
    t = clf.classify("Traceback (most recent call last):\n  File ...")
    assert t.type == TokenType.ERROR


def test_markdown_heading(clf):
    t = clf.classify("## Installation\n")
    assert t.type == TokenType.MARKDOWN


def test_plain_text(clf):
    t = clf.classify("Hello, this is just plain text.")
    assert t.type == TokenType.PLAIN_TEXT


def test_unknown_falls_to_plain(clf):
    # Even random input should resolve to plain_text via catch-all
    t = clf.classify("xyzzy 12345 !!!")
    assert t.type == TokenType.PLAIN_TEXT
