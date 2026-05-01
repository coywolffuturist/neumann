"""Tests for LLM adapters, router, and core types."""
import pytest
from neumann.llm import (
    LLMMessage, LLMResponse, LLMChunk, LLMUsage, LLMProvider,
)
from neumann.llm.adapter import LLMAdapter
from neumann.llm.router import LLMRouter, LLMConfig


# ── Core types ──────────────────────────────────────────────────

class TestCoreTypes:
    def test_llm_message(self):
        msg = LLMMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"

    def test_llm_response(self):
        resp = LLMResponse(
            content="hi",
            provider="openai",
            model="gpt-4o",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        assert resp.content == "hi"
        assert resp.usage.total_tokens == 15

    def test_llm_chunk(self):
        chunk = LLMChunk(delta="hello", finish_reason=None)
        assert chunk.delta == "hello"

    def test_llm_provider_enum(self):
        assert LLMProvider.OPENAI == "openai"
        assert LLMProvider.ANTHROPIC == "anthropic"
        assert LLMProvider.OLLAMA == "ollama"


# ── LLM Config ──────────────────────────────────────────────────

class TestLLMConfig:
    def test_defaults(self):
        cfg = LLMConfig()
        assert cfg.default_openai_model == "gpt-4o"
        assert cfg.default_anthropic_model == "claude-sonnet-4-20250514"
        assert cfg.default_ollama_model == "qwen2.5-coder"
        assert cfg.timeout == 120

    def test_custom(self):
        cfg = LLMConfig(
            openai_api_key="sk-test",
            timeout=60,
            default_openai_model="gpt-4o-mini",
        )
        assert cfg.openai_api_key == "sk-test"
        assert cfg.timeout == 60


# ── LLM Router ──────────────────────────────────────────────────

class TestLLMRouter:
    def test_router_creation(self):
        router = LLMRouter()
        assert router.config is not None

    def test_router_with_custom_config(self):
        cfg = LLMConfig(timeout=30)
        router = LLMRouter(config=cfg)
        assert router.config.timeout == 30

    def test_router_no_adapters_by_default(self):
        # Without API keys or running Ollama, router should have no adapters
        router = LLMRouter()
        # At least we can test the interface
        assert isinstance(router.list_adapters(), list)

    def test_router_set_default_model(self):
        router = LLMRouter()
        router.set_default_model("gpt-4o")
        assert router._default_model == "gpt-4o"

    def test_router_register_adapter(self):
        router = LLMRouter()

        class FakeAdapter(LLMAdapter):
            @property
            def provider(self):
                return LLMProvider.OPENAI

            def available_models(self):
                return ["fake"]

            def chat(self, messages, model="fake", **kwargs):
                return LLMResponse(
                    content="fake response",
                    provider=self.provider.value,
                    model=model,
                )

            def stream(self, messages, model="fake", **kwargs):
                from collections.abc import Generator
                yield LLMChunk(delta="fake")
                yield LLMChunk(finish_reason="stop")

            async def astream(self, messages, model="fake", **kwargs):
                yield LLMChunk(delta="fake")
                yield LLMChunk(finish_reason="stop")

        router.register("fake", FakeAdapter())
        assert "fake" in router.list_adapters()
        assert router.get_adapter("fake") is not None

    def test_router_unregister(self):
        router = LLMRouter()
        router.register("test", type("T", (LLMAdapter,), {
            "provider": property(lambda self: LLMProvider.OPENAI),
            "available_models": lambda self: [],
            "chat": lambda self, **kw: LLMResponse(),
            "stream": lambda self, **kw: iter([]),
            "astream": lambda self, **kw: _async_iter([]),
        })())
        router.unregister("test")
        assert "test" not in router.list_adapters()

    def test_resolve_openai_model(self):
        router = LLMRouter()
        # Even without the adapter, the routing logic should identify it as OpenAI
        adapter, model = router._resolve_adapter("gpt-4o")
        assert model == "gpt-4o"

    def test_resolve_anthropic_model(self):
        router = LLMRouter()
        adapter, model = router._resolve_adapter("claude-sonnet-4")
        assert model == "claude-sonnet-4"

    def test_build_messages(self):
        messages = LLMRouter._build_messages(
            "hello", "You are helpful", None
        )
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[0].content == "You are helpful"
        assert messages[1].role == "user"
        assert messages[1].content == "hello"

    def test_build_messages_with_history(self):
        history = [LLMMessage(role="user", content="first")]
        messages = LLMRouter._build_messages("second", None, history)
        assert len(messages) == 2
        assert messages[0].content == "first"
        assert messages[1].content == "second"

    def test_chat_fails_without_adapter(self):
        router = LLMRouter()
        with pytest.raises(RuntimeError, match="No LLM adapter"):
            router.chat("hello")

    def test_stream_fails_without_adapter(self):
        router = LLMRouter()
        with pytest.raises(RuntimeError, match="No LLM adapter"):
            list(router.stream("hello"))


# ── Adapters can be imported ─────────────────────────────────────

class TestAdapterImports:
    def test_openai_adapter_exists(self):
        from neumann.llm.openai_adapter import OpenAIAdapter
        assert OpenAIAdapter is not None

    def test_anthropic_adapter_exists(self):
        from neumann.llm.anthropic_adapter import AnthropicAdapter
        assert AnthropicAdapter is not None

    def test_ollama_adapter_exists(self):
        from neumann.llm.ollama_adapter import OllamaAdapter
        assert OllamaAdapter is not None


def _async_iter(items):
    """Helper to create an async generator from a list."""
    async def gen():
        for item in items:
            yield item
    return gen()
