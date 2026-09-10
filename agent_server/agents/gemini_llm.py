"""Google Gemini client implementing the same LLMClient Protocol as llm.py.

Speaks Gemini's OpenAI-COMPATIBLE endpoint rather than the google-genai SDK:
`openai` is already a dependency, and the compatibility layer covers everything
the agents use (system prompt, multi-turn messages, tool calling), so this adds a
provider without adding a package.

Auth is a Google AI Studio API key (https://aistudio.google.com/apikey) in
GEMINI_API_KEY. NOTE: Vertex AI is a different product with ADC/service-account
auth — this client does not speak to it.

The return shape is normalised to match AnthropicLLM.complete() exactly:

    {"text": str, "tool_calls": [{"name": str, "input": dict, "id": str}, ...]}

so the agents cannot tell which provider answered. That matters because
discovery/research drive tool-use loops off `tool_calls`.
"""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from agent_server.config import CONFIG
from agent_server.log import get_logger

log = get_logger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _vertex_access_token() -> str:
    """Mint a short-lived OAuth token from Application Default Credentials.

    Refreshed per client build rather than cached: ADC tokens last ~1h, and a
    long-running scheduler would otherwise serve a stale one after an idle
    stretch.
    """
    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise RuntimeError(
            f"Vertex AI dependencies not installed ({exc}). "
            "Run: pip install google-auth requests"
        ) from exc

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    if not credentials.token:
        raise RuntimeError("ADC returned no token.")
    return credentials.token


def _to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """Convert Anthropic tool schemas to the OpenAI function-tool shape.

    Anthropic: {"name", "description", "input_schema"}
    OpenAI:    {"type": "function", "function": {"name", "description", "parameters"}}

    The agents author tools in Anthropic form (that is the frozen shape in
    CONTRACTS.md §8), so the translation lives here rather than leaking a
    provider difference into every agent.
    """
    if not tools:
        return None
    converted: list[dict] = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """Flatten Anthropic content blocks into plain OpenAI message strings.

    Anthropic allows `content` to be a list of typed blocks; the OpenAI surface
    wants a string. Only text is carried across — tool RESULTS are rendered as
    text so a tool-use loop still reads coherently to the model.
    """
    out: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": message.get("role", "user"), "content": content})
            continue
        parts: list[str] = []
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                inner = block.get("content")
                parts.append(inner if isinstance(inner, str) else json.dumps(inner))
        out.append({"role": message.get("role", "user"), "content": "\n".join(parts)})
    return out


class GeminiLLM:
    """Gemini wrapper matching AnthropicLLM's interface.

    Args:
        client: Optional pre-built OpenAI-compatible client, for tests.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _use_vertex(self) -> bool:
        return (CONFIG.llm_provider or "").lower() == "vertex"

    def _model_id(self) -> str:
        return CONFIG.vertex_model if self._use_vertex() else CONFIG.gemini_model

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if self._use_vertex():
            # Vertex: same models, billed to a GCP project, ADC auth. Used when
            # an org policy disallows API keys.
            if not CONFIG.vertex_project:
                raise RuntimeError(
                    "VERTEX_PROJECT not set. Add it to agent_server/.env and run "
                    "`gcloud auth application-default login`."
                )
            base = (
                f"https://{CONFIG.vertex_location}-aiplatform.googleapis.com/v1/"
                f"projects/{CONFIG.vertex_project}/locations/{CONFIG.vertex_location}"
                "/endpoints/openapi"
            )
            self._client = OpenAI(base_url=base, api_key=_vertex_access_token())
            log.debug("llm_client_vertex", project=CONFIG.vertex_project,
                      model=CONFIG.vertex_model)
            return self._client

        if not CONFIG.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Get one at https://aistudio.google.com/apikey "
                "and add it to agent_server/.env (or inject a client in tests)."
            )
        self._client = OpenAI(base_url=BASE_URL, api_key=CONFIG.gemini_api_key)
        log.debug("llm_client_gemini", model=CONFIG.gemini_model)
        return self._client

    def complete(
        self,
        system: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Call Gemini and return the SAME normalised dict AnthropicLLM returns."""
        client = self._get_client()
        model_id = self._model_id()

        payload: dict[str, Any] = {
            "model": model_id,
            "max_tokens": 4096,
            # The system prompt becomes a leading system message.
            "messages": [{"role": "system", "content": system}, *_to_openai_messages(messages)],
        }
        converted_tools = _to_openai_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        log.debug("llm_complete", model=model_id, n_messages=len(messages))
        response = client.chat.completions.create(**payload)

        choice = response.choices[0]
        text = choice.message.content or ""

        tool_calls: list[dict[str, Any]] = []
        for call in getattr(choice.message, "tool_calls", None) or []:
            raw_args = getattr(call.function, "arguments", "") or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except (ValueError, TypeError):
                # A model can emit malformed JSON args; degrade to an empty dict
                # rather than crashing the agent loop mid-run.
                log.warning("gemini_tool_args_unparseable", raw=str(raw_args)[:200])
                parsed_args = {}
            tool_calls.append(
                {"name": call.function.name, "input": parsed_args, "id": call.id}
            )

        log.debug(
            "llm_complete_done",
            n_tool_calls=len(tool_calls),
            stop_reason=choice.finish_reason,
        )
        return {"text": text, "tool_calls": tool_calls}
