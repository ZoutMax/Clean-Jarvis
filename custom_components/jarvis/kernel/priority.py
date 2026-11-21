"""Emergency / priority hierarchy (kernel hardening H2, docs/KERNEL_PLAN.md).

One explicit precedence ladder so competing concerns resolve the same way
everywhere: **life safety → security → property → household → convenience →
personality**. The invariant the audit calls out is encoded here — *personality
must never override a safety concern* — as a pure comparison, so attention,
authority and the planner can consult one source of truth instead of ad-hoc
`if urgent` checks.

Pure: no Home Assistant import, no I/O.
"""
from __future__ import annotations

from typing import Dict, Optional

# Tiers, highest precedence first. The integer rank is what code compares; higher
# rank wins. Keep the gaps so tiers can be inserted later without renumbering.
LIFE_SAFETY = "life_safety"
SECURITY = "security"
PROPERTY = "property"
HOUSEHOLD = "household"
CONVENIENCE = "convenience"
PERSONALITY = "personality"

_RANK: Dict[str, int] = {
    LIFE_SAFETY: 60,
    SECURITY: 50,
    PROPERTY: 40,
    HOUSEHOLD: 30,
    CONVENIENCE: 20,
    PERSONALITY: 10,
}

# Ordered highest → lowest, for iteration/display.
ORDER = [LIFE_SAFETY, SECURITY, PROPERTY, HOUSEHOLD, CONVENIENCE, PERSONALITY]

# The lowest tier that counts as a safety concern personality can never override.
_SAFETY_FLOOR = _RANK[SECURITY]


def rank(tier: str) -> int:
    """Numeric precedence for a tier (0 for an unknown tier — lowest)."""
    return _RANK.get(tier, 0)


def is_safety(tier: str) -> bool:
    """True for tiers that are safety concerns (life safety or security)."""
    return rank(tier) >= _SAFETY_FLOOR


def outranks(a: str, b: str) -> bool:
    """True if tier ``a`` takes precedence over tier ``b``."""
    return rank(a) > rank(b)


def winner(a: str, b: str) -> str:
    """The higher-precedence of two tiers (``a`` wins ties — caller order)."""
    return a if rank(a) >= rank(b) else b


def may_override(actor_tier: str, target_tier: str) -> bool:
    """May an ``actor_tier`` concern override a ``target_tier`` one?

    Enforces the core invariant: **personality never overrides a safety concern**,
    and more generally a lower tier never overrides a strictly higher one. Equal
    tiers may override (same-level arbitration is the caller's business).
    """
    if actor_tier == PERSONALITY and is_safety(target_tier):
        return False
    return rank(actor_tier) >= rank(target_tier)
