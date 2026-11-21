"""Attention / interruption arbitration (kernel Phase 6, docs/KERNEL_PLAN.md).

Centralises the "may JARVIS interrupt right now?" decision that today lives in
`output_gate` plus the adaptive interruption budget. Given a request's priority
and the current context (recent interruptions, a remaining budget, quiet hours,
an explicit shush), it returns **ALLOW / DEFER / SUPPRESS** with a reason.

Pure: no Home Assistant import, no I/O — the live context is passed in as an
`AttentionContext`, so the arbitration is deterministic and can be parity-checked
against the current gate before anything delegates to it. Phase 6 ships it
additively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# decisions
ALLOW = "allow"
DEFER = "defer"      # hold for later (budget/quiet) — not dropped
SUPPRESS = "suppress"  # drop (shushed, or a duplicate of something just said)

# priority tiers (higher = more important)
CRITICAL = 100   # safety/security — always interrupts
HIGH = 70
NORMAL = 40
LOW = 10


@dataclass(frozen=True)
class AttentionContext:
    """Live state the arbiter reasons over (supplied by the caller)."""

    budget_remaining: float = 1.0      # 0..1 of the adaptive interruption budget
    recent_interruptions: int = 0      # in the recent window
    max_recent: int = 3                # soft cap before NORMAL/LOW defer
    quiet_hours: bool = False          # sleeping / do-not-disturb
    shushed: bool = False              # explicit user shush in effect
    duplicate: bool = False            # same content was just announced


@dataclass(frozen=True)
class AttentionRequest:
    category: str
    priority: int = NORMAL
    note: str = ""


@dataclass(frozen=True)
class AttentionDecision:
    decision: str
    reason: str
    priority: int

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


def arbitrate(request: AttentionRequest, context: AttentionContext) -> AttentionDecision:
    """Decide whether an interruption may happen now.

    CRITICAL always interrupts (safety first) — it ignores budget, quiet hours and
    shush, but a genuine duplicate is still suppressed. Everything else yields to a
    shush/duplicate (SUPPRESS), then to quiet hours / exhausted budget / too many
    recent interruptions (DEFER), otherwise ALLOW.
    """
    pri = request.priority
    critical = pri >= CRITICAL

    # A duplicate of something just said is always dropped — even critical, since
    # repeating it adds nothing.
    if context.duplicate:
        return AttentionDecision(SUPPRESS, "duplicate of a recent announcement", pri)

    if critical:
        return AttentionDecision(ALLOW, "critical priority overrides gating", pri)

    # Explicit user shush drops non-critical interruptions.
    if context.shushed:
        return AttentionDecision(SUPPRESS, "user shush in effect", pri)

    # Quiet hours defer all but HIGH+.
    if context.quiet_hours and pri < HIGH:
        return AttentionDecision(DEFER, "quiet hours", pri)

    # Budget exhausted → defer non-HIGH.
    if context.budget_remaining <= 0.0 and pri < HIGH:
        return AttentionDecision(DEFER, "interruption budget exhausted", pri)

    # Too many recent interruptions → defer the low-importance ones.
    if context.recent_interruptions >= context.max_recent and pri <= NORMAL:
        return AttentionDecision(DEFER, "too many recent interruptions", pri)

    return AttentionDecision(ALLOW, "within attention budget", pri)
