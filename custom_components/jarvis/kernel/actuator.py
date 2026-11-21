"""Universal actuator contract (MCU audit A5).

The audit's point 17 — "perhaps the most important implementation rule to
establish": every way JARVIS acts on the home (`control_device`, `bulk_control`,
`execute_plan`, `goals`, FRIDAY/HOMER, automations, intrusion, proactive) should
converge on **one** request shape, so there is a single description of *who, with
what authority, intended what, on what target, expecting what outcome* — instead
of several independent "ways of being autonomous".

This module is that shape:

    ActuatorRequest → (Authority) → (Preconditions) → Actuator →
    (Postconditions) → ActuatorOutcome

It is a pure data contract — no Home Assistant import, no execution. Callers build
an ``ActuatorRequest`` and, as each actuator migrates, route through it; today
``control_device`` constructs one in **shadow** (logs it, no behaviour change) so
the shape is exercised on real traffic before anything depends on it. The outcome
model (``requested → executed → observed → verified``) is the audit's point 18:
"action succeeded" is not "outcome achieved".
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ActuatorRequest:
    """One canonical request to change the world.

    Every actuator path should be expressible as one of these. Fields beyond
    ``capability``/``target`` are optional so a caller supplies what it has while
    the contract spreads — but the richer it is, the more the kernel can reason
    (authority, correlation, idempotency, verification).
    """

    capability: str                      # e.g. "light.turn_on" (domain.service)
    target: str = ""                     # entity id / area the action acts on
    params: Dict[str, Any] = field(default_factory=dict)
    actor: str = "jarvis"                # who is acting (jarvis / friday / homer / user)
    identity: Optional[str] = None       # human on whose behalf / present
    intent: Optional[str] = None         # why — a short human phrase
    situation: Optional[str] = None      # the active home situation, if any
    authority: Optional[str] = None      # the authority decision (allow/confirm/deny), once wired
    correlation_id: Optional[str] = None  # the event/turn this traces to
    causation_id: Optional[str] = None   # the specific cause that triggered it
    idempotency_key: Optional[str] = None  # makes a retry/replay safe
    expected_outcome: Optional[str] = None  # the state change we expect to verify
    id: str = field(default_factory=lambda: "act_" + uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


# Outcome lifecycle (the audit's point 18 — a complete outcome model).
REQUESTED = "requested"
EXECUTED = "executed"      # the service call returned success
OBSERVED = "observed"      # live state was read back
VERIFIED = "verified"      # observed state matches expected_outcome
MISMATCH = "mismatch"      # executed, but the world didn't reach expected_outcome
FAILED = "failed"          # the action itself failed


@dataclass(frozen=True)
class ActuatorOutcome:
    """What actually happened to an :class:`ActuatorRequest`.

    Distinguishes *"the HA service returned success"* (``EXECUTED``) from *"the
    world actually reached the expected state"* (``VERIFIED``) — the gap the audit
    flagged (HA says the light turned off; it's still on)."""

    request_id: str
    status: str = REQUESTED
    observed: Optional[str] = None       # the state read back after acting
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def build_actuator_request(capability: str, *, target: str = "",
                           params: Optional[dict] = None, **kw) -> ActuatorRequest:
    """Convenience builder; keeps call sites short as paths migrate onto the
    contract. Unknown kwargs are rejected by the dataclass, so a typo fails loudly
    rather than being silently dropped."""
    return ActuatorRequest(capability=capability, target=target,
                           params=dict(params or {}), **kw)
