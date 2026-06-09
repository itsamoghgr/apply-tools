"""FROZEN runtime-agent dependency bundle (see CONTRACTS.md §8).

The agentic leaves (discovery, research) receive everything they need through
`AgentDeps` instead of importing the trunk. This keeps agency in the leaves and
makes the agents trivially testable with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    from agent_server.web import FetchedPage, SearchResult


class LLMClient(Protocol):
    """Thin Anthropic wrapper the agents reason with. Implemented by
    agents/llm.py (discovery-agent builder owns it)."""

    def complete(
        self, system: str, messages: list[dict], *, tools: list[dict] | None = None
    ) -> dict:
        """Return a normalized response dict: {"text": str, "tool_calls": [...]}."""
        ...


@dataclass
class AgentDeps:
    """What a runtime agent needs, injected by the orchestrator/runner.

    `audit(stage, event, data)` writes one row to audit_traces (job_id is bound
    when the runner builds deps for a specific job).
    """

    search: "Callable[..., list[SearchResult]]"
    fetch_page: "Callable[..., FetchedPage]"
    llm: LLMClient
    audit: Callable[[str, str, dict], None]
    normalize_domain: Callable[[str], "str | None"]
