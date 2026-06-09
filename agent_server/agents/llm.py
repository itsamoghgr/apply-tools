"""Thin Anthropic wrapper implementing the LLMClient Protocol (CONTRACTS.md §8).

Both runtime agents (discovery, research) share this client. It speaks the
Anthropic Messages API, which is identical whether the backing client is the
direct ``anthropic.Anthropic`` (CONFIG.llm_provider == "anthropic") or
``anthropic.AnthropicBedrock`` (CONFIG.llm_provider == "bedrock", AWS Claude).
Only client construction and the model id differ by provider; the request and
response-normalisation code is shared.

Usage::

    from agent_server.agents.llm import AnthropicLLM
    llm = AnthropicLLM()                       # provider from CONFIG
    resp = llm.complete("You are …", [{"role": "user", "content": "Hi"}])
    # resp == {"text": "...", "tool_calls": [...]}
"""

from __future__ import annotations

from typing import Any

import anthropic

from agent_server.config import CONFIG
from agent_server.log import get_logger

log = get_logger(__name__)


class AnthropicLLM:
    """Normalised Anthropic Messages wrapper (direct API or AWS Bedrock).

    Args:
        client: Optional pre-built client (``anthropic.Anthropic`` or
                ``anthropic.AnthropicBedrock``). Pass one in tests to avoid
                network calls. When omitted, the client + model id are resolved
                from CONFIG.llm_provider.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client  # may be None; resolved lazily below

    def _model_id(self) -> str:
        """The model id to send, selected by provider."""
        if CONFIG.llm_provider == "bedrock":
            return CONFIG.bedrock_model
        return CONFIG.llm_model

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if CONFIG.llm_provider == "bedrock":
            # Auth via the standard AWS credential chain (env / ~/.aws / IAM).
            # No explicit keys passed here — boto3 resolves them, mirroring the
            # platform backend.
            self._client = anthropic.AnthropicBedrock(aws_region=CONFIG.bedrock_region)
            log.debug("llm_client_bedrock", region=CONFIG.bedrock_region, model=CONFIG.bedrock_model)
            return self._client

        if not CONFIG.anthropic_api_key:
            raise RuntimeError(
                "No LLM credentials: set AGENT_LLM_PROVIDER=bedrock with AWS creds, "
                "or ANTHROPIC_API_KEY for the direct API (or inject a client in tests)."
            )
        self._client = anthropic.Anthropic(api_key=CONFIG.anthropic_api_key)
        log.debug("llm_client_anthropic", model=CONFIG.llm_model)
        return self._client

    def complete(
        self,
        system: str,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Call the Anthropic Messages API and return a normalised response dict.

        Returns::

            {
                "text": "<joined text from all TextBlock content>",
                "tool_calls": [
                    {"name": str, "input": dict, "id": str},
                    ...
                ],
            }
        """
        client = self._get_client()
        model_id = self._model_id()

        kwargs: dict[str, Any] = dict(
            model=model_id,
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        log.debug("llm_complete", model=model_id, n_messages=len(messages))

        response = client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "name": block.name,
                        "input": block.input,
                        "id": block.id,
                    }
                )
            # Ignore other block types (thinking, etc.)

        result = {"text": "\n".join(text_parts), "tool_calls": tool_calls}
        log.debug(
            "llm_complete_done",
            n_tool_calls=len(tool_calls),
            stop_reason=response.stop_reason,
        )
        return result
