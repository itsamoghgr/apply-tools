"""Gemini client tests.

The contract that matters: GeminiLLM must be indistinguishable from AnthropicLLM
to the agents. Same normalised {"text", "tool_calls"} shape, same Anthropic-style
tool schemas going in — otherwise the discovery/research tool-use loops break.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from agent_server.agents import llm as llm_mod
from agent_server.agents.gemini_llm import (
    GeminiLLM,
    _to_openai_messages,
    _to_openai_tools,
)


def fake_response(text="hi", tool_calls=None, finish="stop"):
    message = SimpleNamespace(content=text, tool_calls=tool_calls or [])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


class FakeClient:
    """Captures the outgoing payload so tests can assert on the wire shape."""

    def __init__(self, response=None):
        self.response = response or fake_response()
        self.captured = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.captured = kwargs
        return self.response


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------

def test_returns_the_same_shape_as_anthropic():
    llm = GeminiLLM(client=FakeClient(fake_response("hello world")))
    result = llm.complete("sys", [{"role": "user", "content": "hi"}])
    assert result == {"text": "hello world", "tool_calls": []}


def test_tool_calls_are_normalised_to_anthropic_shape():
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="web_search", arguments='{"query": "ai jobs"}'),
    )
    llm = GeminiLLM(client=FakeClient(fake_response("", [call])))
    result = llm.complete("sys", [{"role": "user", "content": "hi"}])
    assert result["tool_calls"] == [
        {"name": "web_search", "input": {"query": "ai jobs"}, "id": "call_1"}
    ]


def test_malformed_tool_arguments_do_not_crash_the_loop():
    """A model can emit invalid JSON args; degrade rather than abort a run."""
    call = SimpleNamespace(
        id="c1", function=SimpleNamespace(name="f", arguments="{not json")
    )
    llm = GeminiLLM(client=FakeClient(fake_response("", [call])))
    result = llm.complete("sys", [{"role": "user", "content": "hi"}])
    assert result["tool_calls"][0]["input"] == {}


def test_none_content_becomes_empty_string():
    llm = GeminiLLM(client=FakeClient(fake_response(None)))
    assert llm.complete("s", [{"role": "user", "content": "x"}])["text"] == ""


# ---------------------------------------------------------------------------
# Request translation
# ---------------------------------------------------------------------------

def test_system_prompt_becomes_a_leading_system_message():
    client = FakeClient()
    GeminiLLM(client=client).complete("you are a bot", [{"role": "user", "content": "hi"}])
    messages = client.captured["messages"]
    assert messages[0] == {"role": "system", "content": "you are a bot"}
    assert messages[1] == {"role": "user", "content": "hi"}


def test_anthropic_tool_schema_is_converted():
    """Agents author tools in Anthropic form (frozen in CONTRACTS.md §8)."""
    tools = [{
        "name": "web_search",
        "description": "Search the web",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }]
    converted = _to_openai_tools(tools)
    assert converted == [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    }]


def test_no_tools_sends_no_tools_key():
    client = FakeClient()
    GeminiLLM(client=client).complete("s", [{"role": "user", "content": "hi"}])
    assert "tools" not in client.captured


def test_content_blocks_are_flattened():
    """Anthropic allows block lists; the OpenAI surface wants a string."""
    out = _to_openai_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ]},
    ])
    assert out == [{"role": "user", "content": "part one\npart two"}]


def test_tool_results_survive_as_text():
    """A tool-use loop must still read coherently after flattening."""
    out = _to_openai_messages([
        {"role": "user", "content": [
            {"type": "tool_result", "content": "search returned 3 hits"},
        ]},
    ])
    assert "search returned 3 hits" in out[0]["content"]


def test_string_content_passes_through():
    assert _to_openai_messages([{"role": "user", "content": "plain"}]) == [
        {"role": "user", "content": "plain"}
    ]


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _config_with(**kw):
    return dataclasses.replace(llm_mod.CONFIG, **kw)


@pytest.mark.parametrize("provider,expected", [
    ("gemini", "GeminiLLM"),
    ("anthropic", "AnthropicLLM"),
    ("bedrock", "AnthropicLLM"),
])
def test_build_llm_selects_by_provider(monkeypatch, provider, expected):
    monkeypatch.setattr(llm_mod, "CONFIG", _config_with(llm_provider=provider))
    assert type(llm_mod.build_llm()).__name__ == expected


def test_unknown_provider_falls_back_rather_than_raising(monkeypatch):
    """A typo'd env var should degrade to the default, not kill the service."""
    monkeypatch.setattr(llm_mod, "CONFIG", _config_with(llm_provider="gemeni"))
    assert type(llm_mod.build_llm()).__name__ == "AnthropicLLM"


def test_missing_key_raises_a_useful_message(monkeypatch):
    import agent_server.agents.gemini_llm as gem
    monkeypatch.setattr(gem, "CONFIG", dataclasses.replace(gem.CONFIG, gemini_api_key=None))
    with pytest.raises(RuntimeError, match="aistudio.google.com"):
        GeminiLLM().complete("s", [{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Vertex AI path
#
# Same models and wire format as AI Studio; only the base URL and auth differ.
# This path exists because an org policy can disallow API keys outright, and
# because GCP credits only apply to Vertex.
# ---------------------------------------------------------------------------

import agent_server.agents.gemini_llm as gem


def _gem_config(**kw):
    return dataclasses.replace(gem.CONFIG, **kw)


def test_vertex_provider_uses_vertex_model(monkeypatch):
    monkeypatch.setattr(gem, "CONFIG", _gem_config(
        llm_provider="vertex", vertex_project="p1",
        vertex_model="google/gemini-2.5-pro"))
    client = FakeClient()
    GeminiLLM(client=client).complete("s", [{"role": "user", "content": "hi"}])
    assert client.captured["model"] == "google/gemini-2.5-pro"


def test_ai_studio_provider_uses_gemini_model(monkeypatch):
    monkeypatch.setattr(gem, "CONFIG", _gem_config(
        llm_provider="gemini", gemini_model="gemini-2.5-flash"))
    client = FakeClient()
    GeminiLLM(client=client).complete("s", [{"role": "user", "content": "hi"}])
    assert client.captured["model"] == "gemini-2.5-flash"


def test_vertex_without_project_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(gem, "CONFIG", _gem_config(
        llm_provider="vertex", vertex_project=None))
    with pytest.raises(RuntimeError, match="VERTEX_PROJECT not set"):
        GeminiLLM().complete("s", [{"role": "user", "content": "hi"}])


def test_vertex_base_url_is_region_and_project_scoped(monkeypatch):
    """Vertex endpoints are per-project AND per-region; a wrong URL 404s."""
    monkeypatch.setattr(gem, "CONFIG", _gem_config(
        llm_provider="vertex", vertex_project="my-proj", vertex_location="europe-west4"))
    monkeypatch.setattr(gem, "_vertex_access_token", lambda: "tok")
    captured = {}
    monkeypatch.setattr(gem, "OpenAI",
                        lambda **kw: captured.update(kw) or FakeClient())
    GeminiLLM()._get_client()
    assert "europe-west4-aiplatform.googleapis.com" in captured["base_url"]
    assert "projects/my-proj/locations/europe-west4" in captured["base_url"]
    assert captured["api_key"] == "tok"


def test_build_llm_routes_vertex_to_gemini_client(monkeypatch):
    monkeypatch.setattr(llm_mod, "CONFIG", _config_with(llm_provider="vertex"))
    assert type(llm_mod.build_llm()).__name__ == "GeminiLLM"


def test_vertex_dependency_error_mentions_both_packages():
    """Regression: google.auth.transport.requests needs the `requests` package
    on top of google-auth. Reporting only google-auth sent you in circles when
    google-auth was already installed and the import still failed."""
    import builtins

    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name.startswith("google.auth"):
            raise ImportError("No module named 'requests'")
        return real_import(name, *a, **k)

    builtins.__import__ = blocked
    try:
        with pytest.raises(RuntimeError, match="google-auth requests"):
            gem._vertex_access_token()
    finally:
        builtins.__import__ = real_import
