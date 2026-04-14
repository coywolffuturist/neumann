"""Agent Memory — conversation history, context management, and persistence.

Features:
- Sliding window: keeps recent messages, summarizes old ones
- Persistent storage: save/load conversation state to JSON
- Token budget: auto-truncate to fit within LLM context limits
- Working memory: scratchpad for current task state

Usage:
    from neumann.memory import AgentMemory
    
    memory = AgentMemory(max_tokens=128_000)
    memory.add_user("Write a function to sort a list")
    memory.add_assistant("Here's the code: ...")
    
    # Get messages for LLM (within token budget)
    messages = memory.get_context()
    
    # Save/restore
    memory.save("conversation.json")
    memory.load("conversation.json")
"""
from __future__ import annotations

import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from collections import deque


@dataclass
class MemoryEntry:
    """A single entry in the agent's memory."""
    role: str  # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    token_estimate: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationSummary:
    """Compressed summary of older conversation turns."""
    text: str = ""
    token_estimate: int = 0
    turns_covered: int = 0


class AgentMemory:
    """Manages conversation history with sliding window & persistence."""

    def __init__(
        self,
        max_tokens: int = 128_000,
        max_messages: int = 200,
        system_prompt: str = "",
    ) -> None:
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self.system_prompt = system_prompt

        self._history: deque[MemoryEntry] = deque(maxlen=max_messages)
        self._summary = ConversationSummary()
        self._working_memory: dict[str, Any] = {}
        self._session_id = hashlib.sha256(
            f"session-{time.time()}".encode()
        ).hexdigest()[:8]
        self._total_tokens = 0
        self._turn_count = 0

    # ── Adding messages ───────────────────────────────────────────────

    def add_system(self, content: str, **meta: Any) -> None:
        """Add a system message (usually instructions)."""
        self._add_entry("system", content, **meta)

    def add_user(self, content: str, **meta: Any) -> None:
        """Add a user message."""
        self._add_entry("user", content, **meta)
        self._turn_count += 1

    def add_assistant(self, content: str, **meta: Any) -> None:
        """Add an assistant response."""
        self._add_entry("assistant", content, **meta)
        self._turn_count += 1

    def add_tool_result(self, tool_name: str, content: str, **meta: Any) -> None:
        """Add a tool execution result."""
        self._add_entry(
            "tool",
            content,
            tool_name=tool_name,
            **meta,
        )

    def set_working_memory(self, key: str, value: Any) -> None:
        """Set a key-value in the working memory scratchpad."""
        self._working_memory[key] = value

    def get_working_memory(self, key: str, default: Any = None) -> Any:
        """Get a value from working memory."""
        return self._working_memory.get(key, default)

    def clear_working_memory(self) -> None:
        """Clear the working memory."""
        self._working_memory.clear()

    # ── Getting context for LLM ──────────────────────────────────────

    def get_context(self) -> list[dict[str, str]]:
        """Get the conversation context for the next LLM call.

        Returns messages in the format expected by LLM adapters,
        respecting the token budget. Order:
        1. System prompt
        2. Summary (if exists)
        3. Recent history (within token budget)
        4. Working memory (injected as system context)
        """
        messages: list[dict[str, str]] = []

        # 1. System prompt first
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 2. Summary before history
        if self._summary.text:
            messages.append({
                "role": "system",
                "content": f"[Summary of earlier conversation]\n{self._summary.text}",
            })

        # 3. Add recent history within token budget
        current_tokens = self._estimate_tokens(self.system_prompt) + self._summary.token_estimate
        for entry in reversed(self._history):
            entry_tokens = self._estimate_tokens(entry.content)
            if current_tokens + entry_tokens > self.max_tokens:
                break
            messages.insert(len(messages) - len([m for m in messages if m["role"] == "system"]),
                           {"role": entry.role, "content": entry.content})
            current_tokens += entry_tokens

        # Rebuild messages in correct order
        final: list[dict[str, str]] = []
        for m in messages:
            if m["role"] == "system":
                final.append(m)
        for m in messages:
            if m["role"] != "system":
                final.append(m)

        # 4. Working memory as trailing system context
        if self._working_memory:
            wm_text = json.dumps(self._working_memory, indent=2)
            final.append({
                "role": "system",
                "content": f"[Working Memory]\n{wm_text}",
            })

        return final

    def get_history(self) -> list[MemoryEntry]:
        """Get the full conversation history."""
        return list(self._history)

    # ── Summarization ────────────────────────────────────────────────

    def set_summary(self, text: str) -> None:
        """Manually set a summary of earlier conversation."""
        self._summary = ConversationSummary(
            text=text,
            token_estimate=self._estimate_tokens(text),
            turns_covered=self._turn_count,
        )

    # ── Clearing ──────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all history and working memory."""
        self._history.clear()
        self._working_memory.clear()
        self._summary = ConversationSummary()
        self._total_tokens = 0
        self._turn_count = 0

    def clear_history(self) -> None:
        """Clear only conversation history, keep working memory."""
        self._history.clear()
        self._total_tokens = 0

    # ── Persistence ───────────────────────────────────────────────────

    def save(self, path: str | Path) -> None:
        """Save conversation state to a JSON file."""
        state = {
            "session_id": self._session_id,
            "system_prompt": self.system_prompt,
            "max_tokens": self.max_tokens,
            "max_messages": self.max_messages,
            "history": [e.to_dict() for e in self._history],
            "summary": asdict(self._summary),
            "working_memory": self._working_memory,
            "total_tokens": self._total_tokens,
            "turn_count": self._turn_count,
        }
        Path(path).write_text(json.dumps(state, indent=2, ensure_ascii=False))

    def load(self, path: str | Path) -> None:
        """Load conversation state from a JSON file."""
        data = json.loads(Path(path).read_text())
        self.system_prompt = data.get("system_prompt", "")
        self.max_tokens = data.get("max_tokens", 128_000)
        self.max_messages = data.get("max_messages", 200)
        self._session_id = data.get("session_id", self._session_id)
        self._summary = ConversationSummary(**data.get("summary", {}))
        self._working_memory = data.get("working_memory", {})
        self._total_tokens = data.get("total_tokens", 0)
        self._turn_count = data.get("turn_count", 0)
        self._history = deque(
            [MemoryEntry(**e) for e in data.get("history", [])],
            maxlen=self.max_messages,
        )

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        return {
            "session_id": self._session_id,
            "messages": len(self._history),
            "turns": self._turn_count,
            "total_tokens": self._total_tokens,
            "summary_tokens": self._summary.token_estimate,
            "working_memory_keys": list(self._working_memory.keys()),
        }

    # ── private ───────────────────────────────────────────────────────

    def _add_entry(self, role: str, content: str, **meta: Any) -> None:
        tokens = self._estimate_tokens(content)
        entry = MemoryEntry(
            role=role,
            content=content,
            token_estimate=tokens,
            metadata=meta,
        )
        self._history.append(entry)
        self._total_tokens += tokens

        # Trim oldest entries if over token budget
        while self._total_tokens > self.max_tokens and len(self._history) > 2:
            oldest = self._history.popleft()
            self._total_tokens -= oldest.token_estimate

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count using tiktoken if available, with fallback.
        
        Fallback: ~4 chars per token for English text (conservative).
        """
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except (ImportError, Exception):
            # Fallback: conservative estimate for non-English/code
            # Code and CJK text use more tokens per character
            return len(text) // 3 + 1
