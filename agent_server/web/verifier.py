"""FROZEN verification-waterfall interface (owner decision: free-tier APIs in
order, then SMTP as a weak fallback). Implemented by the verification & delivery
builder.

The gate outputs a continuous confidence SCORE, never a bool. Each provider in
the waterfall is tried until one returns a usable signal; SMTP is the last,
weakest resort (assume residential port 25 blocked + accept-all servers).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmailVerdict:
    """Result of attempting to find + verify a founder email for a domain."""

    email: str | None          # discovered address, if any
    score: float               # 0.0–1.0 confidence in deliverability
    method: str                # "hunter" | "abstract" | "smtp" | "none"
    detail: dict = field(default_factory=dict)  # raw provider response / sub-scores


class Verifier:
    """Runs the configured provider waterfall, then SMTP fallback.

    Implementations live in stages/verify.py helpers. Each provider client
    exposes `find_and_verify(domain, founder_name) -> EmailVerdict | None`,
    returning None to fall through to the next provider.
    """

    def find_and_verify(self, domain: str, founder_name: str | None) -> EmailVerdict:
        raise NotImplementedError
