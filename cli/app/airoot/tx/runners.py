"""Which driver runs a plan — one decision, both callers (draft §128).

`cli.cmd_install` picked a driver by the plan's backend, and `tx.simulate.repair` did not: it always
built a `SimulationRunner`. A real artifact transaction that stopped at the commit point was therefore
resumed by the simulation driver, which re-applied the commit point and then failed its own post-bind
check against an artifact instance -- rolling back an install that would have finished. The rule was
never in doubt (`test_every_boundary_is_recoverable` states it); it simply lived in one caller.

This module is that rule, in one place. It imports both runners *inside* the function because
`artifact` and `simulate` are siblings that must not import each other at module scope.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..clock import Clock, SYSTEM_CLOCK

#: The backend every simulated/fixture plan uses. Anything else means bytes on disk, i.e. the real
#: transaction driver -- same states, same journal, same rollback, different payload.
FIXTURE_BACKEND = "fake_fixture"


def runner_for(
    registry: Any,
    plan: dict[str, Any],
    *,
    clock: Clock = SYSTEM_CLOCK,
    keyring: dict[str, bytes] | None = None,
    injector: Any = None,
) -> Any:
    """The driver this plan's backend calls for."""

    from ..caps.backends import resolve_backend
    from .artifact import ArtifactRunner
    from .simulate import SimulationRunner

    metadata = plan.get("metadata") or {}
    backend_id = str(metadata.get("backend_id") or metadata.get("backend") or FIXTURE_BACKEND)
    if backend_id == FIXTURE_BACKEND:
        return SimulationRunner(registry, clock=clock, keyring=keyring, injector=injector)
    return ArtifactRunner(
        registry,
        resolve_backend(backend_id, root=Path(registry.path).parent.parent),
        clock=clock,
        keyring=keyring,
        injector=injector,
    )


__all__ = ["FIXTURE_BACKEND", "runner_for"]
