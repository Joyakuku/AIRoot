"""The selection policy surface: which machine-level source wins in ``where``.

Draft §6 promotes a steward reference to a first-class candidate and puts its order against
an AIROOT-owned payload under policy control, defaulting to ``steward``. That default is the
whole premise of ADR-0004, so it must be reachable without a flag *and* overridable without
editing code — hence a policy file rather than a constant.

Failure behaviour is deliberately asymmetric:

* the file is **missing or unreadable** → ``steward``, because the steward model is the
  project's premise and a deleted policy file must not silently flip the machine's answer;
* the file exists but says something **invalid** → ``INVALID_INPUT``, because a typo must be
  loud rather than quietly reinterpreted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .. import CLI_ROOT
from ..exits import AirootError

POLICY_PATH = CLI_ROOT / "app" / "airoot" / "policy" / "selection-policy.json"

PRECEDENCE_STEWARD = "steward"
PRECEDENCE_OWNED = "owned"
PRECEDENCES = (PRECEDENCE_STEWARD, PRECEDENCE_OWNED)

DEFAULT_REVISION = "sp-0"

_ALLOWED_KEYS = frozenset({"schema_version", "revision", "precedence", "notes"})


@dataclass(frozen=True)
class SelectionPolicy:
    precedence: str = PRECEDENCE_STEWARD
    revision: str = DEFAULT_REVISION
    source: str = "default"

    @property
    def steward_first(self) -> bool:
        return self.precedence == PRECEDENCE_STEWARD


def load_selection_policy(path: Path | None = None) -> SelectionPolicy:
    """Read the policy file; a missing file is the documented default, not an error."""

    target = Path(path) if path is not None else POLICY_PATH
    if not target.is_file():
        return SelectionPolicy()
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable is treated like missing: the documented default still holds, and the
        # caller reports `source=default` so the degradation is visible in evidence.
        return SelectionPolicy()
    if not isinstance(document, dict):
        raise AirootError(
            "INVALID_INPUT",
            f"selection policy is not a JSON object: {target}",
            evidence=[f"expected one of {', '.join(sorted(_ALLOWED_KEYS))}"],
        )
    unknown = sorted(set(document) - _ALLOWED_KEYS)
    if unknown:
        raise AirootError(
            "INVALID_INPUT",
            f"selection policy has unknown keys: {', '.join(unknown)}",
            evidence=[f"allowed: {', '.join(sorted(_ALLOWED_KEYS))}", str(target)],
        )
    precedence = str(document.get("precedence", PRECEDENCE_STEWARD))
    if precedence not in PRECEDENCES:
        raise AirootError(
            "INVALID_INPUT",
            f"selection policy precedence must be one of {', '.join(PRECEDENCES)}: {precedence!r}",
            evidence=[str(target)],
        )
    return SelectionPolicy(
        precedence=precedence,
        revision=str(document.get("revision", DEFAULT_REVISION)),
        source="file",
    )


__all__ = [
    "DEFAULT_REVISION",
    "POLICY_PATH",
    "PRECEDENCES",
    "PRECEDENCE_OWNED",
    "PRECEDENCE_STEWARD",
    "SelectionPolicy",
    "load_selection_policy",
]
