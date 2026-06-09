"""Unit tests for agent_server/agents/llm.py (AnthropicLLM).

All network calls are mocked — the real Anthropic client is never invoked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_server.agents.llm import AnthropicLLM


# ---------------------------------------------------------------------------
# Helpers to build fake Anthropic response objects
# ---------------------------------------------------------------------------


def _make_text_block(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(
    name: str, input_: dict, id_: str = "toolu_01"
) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_
    block.id = id_
    return block


def _make_response(
    content: list, stop_reason: str = "end_turn"
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnthropicLLMComplete:
    def test_text_only_response(self):
        """A response with only text blocks produces text and empty tool_calls."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [_make_text_block("Hello, world!")]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [{"role": "user", "content": "hi"}])

        assert result["text"] == "Hello, world!"
        assert result["tool_calls"] == []

    def test_multiple_text_blocks_joined(self):
        """Multiple text blocks are joined with a newline."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [_make_text_block("Part 1"), _make_text_block("Part 2")]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [])

        assert result["text"] == "Part 1\nPart 2"
        assert result["tool_calls"] == []

    def test_tool_use_block_mapped(self):
        """A tool_use block is mapped to the normalized tool_calls list."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [
                _make_tool_use_block(
                    "web_search",
                    {"query": "startups 2024"},
                    "toolu_abc",
                )
            ]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [{"role": "user", "content": "find startups"}])

        assert result["text"] == ""
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["name"] == "web_search"
        assert tc["input"] == {"query": "startups 2024"}
        assert tc["id"] == "toolu_abc"

    def test_mixed_text_and_tool_use(self):
        """Responses with both text and tool_use blocks are handled correctly."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [
                _make_text_block("Let me search."),
                _make_tool_use_block(
                    "web_search", {"query": "YC W24"}, "toolu_xyz"
                ),
            ]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [])

        assert result["text"] == "Let me search."
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "web_search"

    def test_multiple_tool_calls(self):
        """Multiple tool_use blocks all appear in tool_calls."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [
                _make_tool_use_block("web_search", {"query": "q1"}, "id1"),
                _make_tool_use_block("fetch_page", {"url": "https://example.com"}, "id2"),
            ]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [])

        assert len(result["tool_calls"]) == 2
        names = {tc["name"] for tc in result["tool_calls"]}
        assert names == {"web_search", "fetch_page"}

    def test_unknown_block_type_ignored(self):
        """Block types other than text and tool_use are silently ignored."""
        fake_client = MagicMock()
        unknown_block = MagicMock()
        unknown_block.type = "thinking"
        fake_client.messages.create.return_value = _make_response(
            [unknown_block, _make_text_block("done")]
        )
        llm = AnthropicLLM(client=fake_client)
        result = llm.complete("sys", [])

        assert result["text"] == "done"
        assert result["tool_calls"] == []

    def test_tools_forwarded_to_client(self):
        """tools= kwarg is forwarded to the Anthropic client."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [_make_text_block("ok")]
        )
        llm = AnthropicLLM(client=fake_client)
        tools = [{"name": "web_search", "input_schema": {"type": "object"}}]
        llm.complete("sys", [], tools=tools)

        _, kwargs = fake_client.messages.create.call_args
        assert kwargs.get("tools") == tools

    def test_no_tools_not_forwarded(self):
        """When tools=None, the 'tools' key is not included in the API call."""
        fake_client = MagicMock()
        fake_client.messages.create.return_value = _make_response(
            [_make_text_block("ok")]
        )
        llm = AnthropicLLM(client=fake_client)
        llm.complete("sys", [], tools=None)

        _, kwargs = fake_client.messages.create.call_args
        assert "tools" not in kwargs

    def test_missing_api_key_raises(self):
        """complete() raises a clear RuntimeError when no API key is configured."""
        # Pass client=None so the lazy-init path runs.
        llm = AnthropicLLM(client=None)
        # Patch CONFIG.anthropic_api_key to None via monkeypatching the module attr.
        import agent_server.agents.llm as llm_module
        original = llm_module.CONFIG.anthropic_api_key

        # Temporarily replace the frozen dataclass attribute via a new Config object.
        from agent_server.config import Config
        import agent_server.agents.llm as llm_mod

        patched_config = Config.__new__(Config)
        # Use object.__setattr__ to bypass frozen
        object.__setattr__(patched_config, "anthropic_api_key", None)
        # Patch every other field to the current CONFIG values
        for field in Config.__dataclass_fields__:
            if field != "anthropic_api_key":
                object.__setattr__(
                    patched_config, field, getattr(llm_mod.CONFIG, field)
                )
        # Force the direct-Anthropic provider so this exercises the missing-key
        # path regardless of the ambient AGENT_LLM_PROVIDER (which may be bedrock).
        object.__setattr__(patched_config, "llm_provider", "anthropic")

        original_config = llm_mod.CONFIG
        llm_mod.CONFIG = patched_config

        try:
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                llm.complete("sys", [])
        finally:
            llm_mod.CONFIG = original_config


class TestBedrockProvider:
    """The Bedrock provider path: client selection + model id by provider."""

    def _patched_config(self, **overrides):
        from agent_server.config import Config
        import agent_server.agents.llm as llm_mod

        cfg = Config.__new__(Config)
        for field in Config.__dataclass_fields__:
            object.__setattr__(cfg, field, getattr(llm_mod.CONFIG, field))
        for k, v in overrides.items():
            object.__setattr__(cfg, k, v)
        return cfg

    def test_bedrock_uses_bedrock_model_id(self):
        from agent_server.agents.llm import AnthropicLLM

        llm = AnthropicLLM()
        import agent_server.agents.llm as llm_mod

        original = llm_mod.CONFIG
        llm_mod.CONFIG = self._patched_config(
            llm_provider="bedrock",
            bedrock_model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        )
        try:
            assert llm._model_id() == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
        finally:
            llm_mod.CONFIG = original

    def test_anthropic_uses_direct_model_id(self):
        from agent_server.agents.llm import AnthropicLLM

        llm = AnthropicLLM()
        import agent_server.agents.llm as llm_mod

        original = llm_mod.CONFIG
        llm_mod.CONFIG = self._patched_config(llm_provider="anthropic", llm_model="claude-opus-4-8")
        try:
            assert llm._model_id() == "claude-opus-4-8"
        finally:
            llm_mod.CONFIG = original
