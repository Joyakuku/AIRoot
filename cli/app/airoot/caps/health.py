"""What is true of a payload **on disk right now** (draft §147).

``health`` is a *recorded* fact: it is written when an instance is registered (``healthy``) or when an
approved change fails and the payload is kept as evidence (``broken``). Nothing re-derives it. Reading
the two readers of that fact side by side found the consequence:

* ``where`` decided a managed payload was usable from the recorded value plus a ``store/`` path check
  — ``usable = healthy and in_store``;
* ``tool status`` walked the same payload and reported which entrypoints were missing.

So the two could answer one question differently, and the one that **selects** was the one reading the
older fact. A payload whose entrypoint was deleted by hand still read as usable to ``where`` while
``tool status`` said the file was gone (measured: §147.1).

This module is the single definition of the on-disk question, for the same reason ``caps/layout.py`` is
the single definition of the payload-marker scan (§71): two readers of one fact must not each keep a
copy of the rule. It **never writes the registry**, so the values this build *records* in ``health``
are still exactly ``healthy`` and ``broken`` (``references/field-values.md``): an observation is not a
record, and saying otherwise would move a documentation fact without giving it a writer.

It is also **not a second selection rule**. The tempting conclusion — ``where`` should refuse a
candidate whose file is gone — is the tightened reading of a fact that admits both, and ADR-0021
settles it the other way: `where` stays a registry-only decision (that is what
`test_where_reads_only_the_registry` covers) and the on-disk verdict is *reported* in the candidate's
evidence. There is deliberately no ``runnable`` property here, because nothing would consume it; the
first attempt at this stage added one and made selection depend on it, which reddened a hand-added
registry row whose payload the simulated transaction never materialises (§147.4).

The verdict uses the vocabulary ``common.schema.json`` already publishes for ``health``, and gives
**one** value that had no writer at all — ``drifted``, for a declaration that drifted out of the only
payload storage the frozen contract allows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exits import AirootError
from ..paths import from_root_relative
from ..registry.entities import is_store_path

#: The on-disk verdicts, in the published `$defs.health` vocabulary. `degraded` and `stale` are
#: deliberately not produced here: both name conditions this observation cannot see (a binding that
#: disagrees with its lifecycle, and a search index older than its policy), and each already has a
#: diagnostic of its own — the same reason `references/field-values.md` gives for keeping them out of
#: this field.
HEALTHY = "healthy"
BROKEN = "broken"
DRIFTED = "drifted"


@dataclass(frozen=True)
class PayloadObservation:
    """The facts about one declared payload, as the filesystem has them right now."""

    store_path: str
    in_store: bool
    payload_present: bool
    entrypoints: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    observed_health: str = HEALTHY
    problems: list[dict[str, str]] = field(default_factory=list)

    def to_document(self) -> dict[str, Any]:
        return {
            "store_path": self.store_path,
            "in_store": self.in_store,
            "payload_present": self.payload_present,
            "entrypoints": list(self.entrypoints),
            "entrypoints_present": list(self.present),
            "entrypoints_missing": list(self.missing),
            "observed_health": self.observed_health,
            "problems": [dict(item) for item in self.problems],
        }


def observe_payload(*, root: Path, store_path: str, entrypoints: list[Any]) -> PayloadObservation:
    """Look at the payload and say what is there. A missing payload is a finding, never a raise.

    A payload that is not under ``store/`` is **drifted** rather than broken: the files may be
    perfectly intact where they are, and what is wrong is the declaration — ``store`` is the only
    payload storage (冻结契约 §5.3), so a row pointing elsewhere cannot be honoured however healthy it
    claims to be (draft §66).
    """

    names = [str(item) for item in entrypoints]
    in_store = is_store_path(store_path)
    problems: list[dict[str, str]] = []
    present: list[str] = []
    missing: list[str] = []

    try:
        store_dir = from_root_relative(store_path, Path(root))
        payload_present = store_dir.is_dir()
    except AirootError as error:
        # A store_path the root cannot resolve is not a location at all; it is reported as absent with
        # the resolver's own words rather than as a traceback (draft §62's lesson).
        store_dir = Path(root)
        payload_present = False
        problems.append({"code": "PAYLOAD_MISSING", "detail": f"store_path cannot be resolved: {error}"})

    if not in_store:
        problems.append(
            {
                "code": "PAYLOAD_OUTSIDE_STORE",
                "detail": f"store_path is not under store/: {store_path}",
            }
        )
        observed = DRIFTED
    elif not payload_present:
        if not problems:
            problems.append({"code": "PAYLOAD_MISSING", "detail": f"{store_path} does not exist"})
        observed = BROKEN
    else:
        for name in names:
            target = store_dir / Path(name)
            (present if target.is_file() else missing).append(name)
        if missing:
            problems.append(
                {
                    "code": "PAYLOAD_MISSING",
                    "detail": f"declared entrypoints are missing from the payload: {', '.join(missing)}",
                }
            )
            observed = BROKEN
        else:
            observed = HEALTHY

    return PayloadObservation(
        store_path=store_path,
        in_store=in_store,
        payload_present=payload_present,
        entrypoints=names,
        present=present,
        missing=missing,
        observed_health=observed,
        problems=problems,
    )


__all__ = ["BROKEN", "DRIFTED", "HEALTHY", "PayloadObservation", "observe_payload"]
