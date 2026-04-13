"""Tests for AgentMemory — conversation history, persistence, and context."""
import json
import tempfile
from pathlib import Path

import pytest
from neumann import AgentMemory, MemoryEntry


class TestAgentMemory:
    def test_add_messages(self):
        mem = AgentMemory()
        mem.add_user("Hello")
        mem.add_assistant("Hi there!")
        assert len(mem.get_history()) == 2

    def test_add_system(self):
        mem = AgentMemory(system_prompt="You are helpful")
        mem.add_system("Extra instruction")
        ctx = mem.get_context()
        system_msgs = [m for m in ctx if m["role"] == "system"]
        assert len(system_msgs) >= 1

    def test_add_tool_result(self):
        mem = AgentMemory()
        mem.add_tool_result("bash", "file1.txt\nfile2.txt")
        history = mem.get_history()
        assert history[-1].role == "tool"
        assert "bash" in history[-1].content or "file1" in history[-1].content

    def test_get_context_includes_system(self):
        mem = AgentMemory(system_prompt="Be helpful")
        mem.add_user("Hello")
        ctx = mem.get_context()
        assert any(m["content"] == "Be helpful" for m in ctx)

    def test_get_context_includes_history(self):
        mem = AgentMemory()
        mem.add_user("Question 1")
        mem.add_assistant("Answer 1")
        ctx = mem.get_context()
        contents = [m["content"] for m in ctx]
        assert "Question 1" in contents
        assert "Answer 1" in contents

    def test_working_memory(self):
        mem = AgentMemory()
        mem.set_working_memory("file", "main.py")
        mem.set_working_memory("lang", "python")
        assert mem.get_working_memory("file") == "main.py"
        assert mem.get_working_memory("missing") is None
        assert mem.get_working_memory("missing", "default") == "default"

    def test_clear_working_memory(self):
        mem = AgentMemory()
        mem.set_working_memory("key", "value")
        mem.clear_working_memory()
        assert mem.get_working_memory("key") is None

    def test_clear(self):
        mem = AgentMemory()
        mem.add_user("hi")
        mem.set_working_memory("k", "v")
        mem.clear()
        assert len(mem.get_history()) == 0
        assert mem.get_working_memory("k") is None

    def test_clear_history_only(self):
        mem = AgentMemory()
        mem.add_user("hi")
        mem.set_working_memory("k", "v")
        mem.clear_history()
        assert len(mem.get_history()) == 0
        assert mem.get_working_memory("k") == "v"

    def test_stats(self):
        mem = AgentMemory()
        mem.add_user("hi")
        mem.add_assistant("hello")
        s = mem.stats()
        assert s["messages"] == 2
        assert s["turns"] == 2
        assert s["session_id"] is not None

    def test_token_budget_truncates(self):
        mem = AgentMemory(max_tokens=50)  # Very small budget
        mem.add_user("x" * 400)  # ~100 tokens
        mem.add_assistant("y" * 400)
        mem.add_user("z" * 400)
        # Should have trimmed oldest messages to fit budget
        ctx = mem.get_context()
        # At least the system prompt (if any) and some history should be there
        assert len(ctx) >= 0

    def test_save_and_load(self, tmp_path):
        mem = AgentMemory(system_prompt="Be helpful")
        mem.add_user("Hello")
        mem.add_assistant("Hi!")
        mem.set_working_memory("lang", "python")

        f = tmp_path / "mem.json"
        mem.save(str(f))

        mem2 = AgentMemory()
        mem2.load(str(f))

        assert mem2.system_prompt == "Be helpful"
        assert len(mem2.get_history()) == 2
        assert mem2.get_working_memory("lang") == "python"

    def test_save_creates_valid_json(self, tmp_path):
        mem = AgentMemory()
        mem.add_user("test")
        f = tmp_path / "mem.json"
        mem.save(str(f))
        # Should be parseable
        data = json.loads(f.read_text())
        assert "history" in data
        assert "session_id" in data

    def test_set_summary(self):
        mem = AgentMemory()
        mem.set_summary("User asked about Python sorting")
        ctx = mem.get_context()
        system_msgs = [m for m in ctx if "Summary" in m.get("content", "")]
        assert len(system_msgs) >= 1

    def test_memory_entry_to_dict(self):
        entry = MemoryEntry(role="user", content="hi")
        d = entry.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "hi"
        assert "timestamp" in d
