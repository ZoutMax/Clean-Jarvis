"""Model / provider router (kernel Phase 6, docs/KERNEL_PLAN.md).

Formalises the tier routing that `llm_provider` does by convention into one
explicit, local-first selection: given a task's requirements (needed capability,
privacy, max latency, max cost, minimum quality) and the currently-available
providers, pick the best allowed provider — preferring local when it can meet the
bar.

Pure: no Home Assistant import, no I/O, no network — providers and their live
availability are passed in, so selection is deterministic and testable, and real
callers feed it `llm_provider`'s tier table. The plan names this `providers/router.py`;
it lives in `kernel/` to share the kernel test harness. Phase 6 ships it additively.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Sequence, Tuple

# privacy levels (higher = stricter; a task's requirement is a floor)
PRIVACY_ANY = 0
PRIVACY_PREFER_LOCAL = 1
PRIVACY_LOCAL_ONLY = 2


@dataclass(frozen=True)
class Provider:
    name: str
    capabilities: FrozenSet[str] = frozenset()
    local: bool = False
    available: bool = True
    latency_ms: float = 0.0     # typical
    cost: float = 0.0           # relative, per call
    quality: float = 0.5        # 0..1 subjective capability/quality


@dataclass(frozen=True)
class TaskRequirements:
    capability: str
    privacy: int = PRIVACY_ANY
    max_latency_ms: Optional[float] = None
    max_cost: Optional[float] = None
    min_quality: float = 0.0


@dataclass(frozen=True)
class RouteResult:
    provider: Optional[Provider]
    reason: str
    considered: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.provider is not None


def _eligible(p: Provider, req: TaskRequirements) -> bool:
    if not p.available:
        return False
    if req.capability not in p.capabilities and "*" not in p.capabilities:
        return False
    if req.privacy >= PRIVACY_LOCAL_ONLY and not p.local:
        return False
    if req.max_latency_ms is not None and p.latency_ms > req.max_latency_ms:
        return False
    if req.max_cost is not None and p.cost > req.max_cost:
        return False
    if p.quality < req.min_quality:
        return False
    return True


def route(req: TaskRequirements, providers: Sequence[Provider]) -> RouteResult:
    """Select the best eligible provider, local-first.

    Ranking: local-first unless privacy is ANY, then higher quality, then lower
    cost, then lower latency. Returns the reason and which providers were eligible.
    """
    eligible = [p for p in providers if _eligible(p, req)]
    if not eligible:
        return RouteResult(None, "no eligible provider for this task",
                           considered=tuple(p.name for p in providers))

    prefer_local = req.privacy >= PRIVACY_PREFER_LOCAL

    def _key(p: Provider):
        # Lower tuple sorts first → most preferred.
        local_rank = 0 if (p.local and prefer_local) else 1
        return (local_rank, -p.quality, p.cost, p.latency_ms)

    best = sorted(eligible, key=_key)[0]
    why = "local-first" if (best.local and prefer_local) else "best quality/cost/latency"
    return RouteResult(best, f"selected {best.name} ({why})",
                       considered=tuple(p.name for p in eligible))
