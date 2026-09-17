"""`path verify` / `path repair`: the PATH-exposure invariant and the entry write (规划 §8.1 rule 8).

The rule is old and absolute:

* the machine PATH may contain **exactly one** AIROOT entry, ``AIROOT\\cli\\exposure\\bin``;
* a version directory never goes on PATH directly;
* Zone W (user-writable) never enters the machine PATH.

Nothing checked it until this module existed, and an invariant that only exists in prose is not an
invariant. It reads the machine and user PATH and reports deviations. It **never writes PATH** — that
belongs to the protected broker (P2), so this side of the module is purely a diagnosis.

**ADR-0050 changed what ``launcher_present`` means.** It used to be "the sanctioned *directory*
exists", which is true on a freshly created root and says nothing about whether anything is callable.
It now means "**at least one stable entry exists**", which is what the minimum version's judgement 10
asks about, and the same step added the two ways that can drift: an active binding with no entry, and
an entry that is not the bytes this build would write (a hand edit, a moved interpreter, a moved
checkout). Both are ``PATH_EXPOSURE_VIOLATION`` — the exposure surface disagreeing with the bindings
is exactly the drift this verb is for, and the frozen code table is not widened for a second name.

One reason code is enough (``PATH_EXPOSURE_VIOLATION``, exit 2): this is *drift from the contract*,
not AIROOT being broken, and the per-finding ``severity`` already carries the gradation. Encoding
severity again as more reason codes would only blur the mapping table.

**`path repair` is the write half of the same surface** (draft §165), and it is the *only* writer this
module adds: it puts back the stable entries that derive from the active machine-level bindings, using
the same `caps.launcher.write_launcher` the ``EXPOSED`` step calls. It never deletes — the other
direction of the drift (an entry with no binding) is `retire`'s business, because the entry is that
binding's projection — and it never writes PATH.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exits import AirootError
from ..paths import is_within
from .launcher import discover_launchers, launcher_directory, write_launcher

SANCTIONED_RELATIVE = ("cli", "exposure", "bin")


def sanctioned_entry(root: Path) -> Path:
    """The one directory allowed on the machine PATH (规划 §8.1 rule 8)."""

    return Path(root).joinpath(*SANCTIONED_RELATIVE)


def _normalize(entry: str) -> str:
    return os.path.normcase(os.path.normpath(entry.strip().strip('"')))


@dataclass
class PathFinding:
    severity: str
    code: str
    detail: str

    def to_document(self) -> dict[str, Any]:
        return {"severity": self.severity, "code": self.code, "detail": self.detail}


@dataclass
class PathVerification:
    root: str
    sanctioned: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    findings: list[PathFinding] = field(default_factory=list)
    launcher_present: bool = False
    launchers: list[dict[str, Any]] = field(default_factory=list)
    expected_launchers: list[str] = field(default_factory=list)

    @property
    def violations(self) -> int:
        return sum(1 for finding in self.findings if finding.severity in {"warning", "error"})

    def reason_code(self) -> str:
        return "PATH_EXPOSURE_VIOLATION" if self.violations else "SUCCESS"

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "root": self.root,
            "sanctioned_entry": self.sanctioned,
            "launcher_present": self.launcher_present,
            "launchers": self.launchers,
            "expected_launchers": self.expected_launchers,
            "entries": self.entries,
            "findings": [finding.to_document() for finding in self.findings],
            "violations": self.violations,
            "path_written": False,
            "reason_code": self.reason_code(),
        }


def verify_path_exposure(
    root: Path,
    *,
    machine_entries: list[str],
    user_entries: list[str],
    expected_launchers: list[str] | tuple[str, ...] = (),
    bindings_note: str | None = None,
) -> PathVerification:
    """Report every AIROOT-owned PATH entry, every stable entry, and every deviation from the rule.

    ``expected_launchers`` is the caller's answer to "which capabilities have an active machine-level
    binding" (ADR-0050). It is passed in rather than read here so this module stays a pure function of
    its inputs: the PATH lists are inputs, and so is the expectation. ``bindings_note`` carries the
    case where the caller could not ask.
    """

    root = Path(root)
    expected = sanctioned_entry(root)
    verification = PathVerification(root=str(root), sanctioned=str(expected))

    found = discover_launchers(root)
    verification.launchers = [item.to_document() for item in found]
    verification.launcher_present = bool(found)
    verification.expected_launchers = sorted(set(str(item) for item in expected_launchers))

    seen: dict[str, list[str]] = {}
    for scope, entries in (("machine", machine_entries), ("user", user_entries)):
        for raw in entries:
            if not raw.strip():
                continue
            candidate = Path(raw.strip().strip('"'))
            try:
                inside = is_within(candidate, root)
            except Exception:  # pragma: no cover - a malformed entry is still worth reporting
                inside = False
            if not inside:
                continue
            is_sanctioned = _normalize(str(candidate)) == _normalize(str(expected))
            verification.entries.append(
                {
                    "scope": scope,
                    "path": str(candidate),
                    "sanctioned": is_sanctioned,
                }
            )
            seen.setdefault(_normalize(str(candidate)), []).append(scope)
            store_dir = root / "store"
            if is_within(candidate, store_dir):
                # A store/version directory on PATH is the exact thing the rule forbids: it pins a
                # single version and bypasses the launcher indirection.
                verification.findings.append(
                    PathFinding(
                        "error",
                        "PATH_EXPOSURE_VIOLATION",
                        f"{scope} PATH contains a store payload directory: {candidate}",
                    )
                )
            elif not is_sanctioned:
                verification.findings.append(
                    PathFinding(
                        "warning",
                        "PATH_EXPOSURE_VIOLATION",
                        f"{scope} PATH contains an AIROOT directory that is not the sanctioned entry: {candidate}",
                    )
                )

    for normalized, scopes in seen.items():
        if len(scopes) > 1:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"{normalized} appears {len(scopes)} times ({', '.join(scopes)}); the rule allows exactly one",
                )
            )

    if not verification.launcher_present:
        # Still information rather than a defect: a root with no managed install legitimately has no
        # stable entry. What is *not* information is an expectation this directory fails to satisfy --
        # that is reported below, per capability.
        verification.findings.append(
            PathFinding(
                "info",
                "EXPOSURE_NOT_IMPLEMENTED",
                f"no stable entry exists in {launcher_directory(root)}; a managed install writes one "
                "when it exposes a binding (ADR-0050)",
            )
        )

    present = {str(item["capability_id"]) for item in verification.launchers}
    for capability_id in verification.expected_launchers:
        if capability_id not in present:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"{capability_id} has an active machine-level binding but no stable entry in "
                    f"{launcher_directory(root)}; nothing exposes it by a version-independent name",
                )
            )
    for capability_id in sorted(present - set(verification.expected_launchers)):
        verification.findings.append(
            PathFinding(
                "warning",
                "PATH_EXPOSURE_VIOLATION",
                f"a stable entry exists for {capability_id} but no active binding exposes it; the "
                "entry resolves nothing (a leftover from a root that was retired before `retire` "
                "cleared its own entry, or a file written by hand)",
            )
        )
    for item in verification.launchers:
        if not item["matches_current"]:
            verification.findings.append(
                PathFinding(
                    "warning",
                    "PATH_EXPOSURE_VIOLATION",
                    f"the stable entry for {item['capability_id']} is not what this build writes "
                    f"({item['path']}): edited by hand, or the interpreter/checkout moved",
                )
            )

    if bindings_note is not None:
        verification.findings.append(PathFinding("info", "EXPOSURE_NOT_IMPLEMENTED", bindings_note))

    return verification


# --------------------------------------------------------------------------- #
# `path repair`: the write half
# --------------------------------------------------------------------------- #


@dataclass
class PathRepair:
    """What a repair attempt did, per capability — reported, never assumed."""

    root: str
    expected: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    rewritten: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    #: Entries that exist and that **no** active machine-level binding projects. `path repair` writes
    #: and never deletes (规划 §9.6 puts root-internal deletion behind a plan plus an approval), so
    #: they are reported instead of removed — and reported with exit 2, because saying `SUCCESS` about
    #: a root `path verify` calls drifting is the contradiction this report face exists to avoid.
    orphans: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    launchers: list[dict[str, Any]] = field(default_factory=list)
    #: Always ``None`` on a document that gets printed: a repair refuses when the expectation cannot
    #: be derived (see `repair_path_exposure`). The key exists because `path verify` carries it, and a
    #: reader of the two documents should not have to learn two field sets.
    bindings_note: str | None = None

    def reason_code(self) -> str:
        # Same code and same tier as `path verify` — "the result is usable, the state is not perfect".
        return "PATH_EXPOSURE_VIOLATION" if self.orphans else "SUCCESS"

    def status(self) -> str:
        return "degraded" if self.orphans else "ok"

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": 1,
            "root": self.root,
            "status": self.status(),
            "expected": list(self.expected),
            "repaired": list(self.repaired),
            "created": list(self.created),
            "rewritten": list(self.rewritten),
            "already_present": list(self.already_present),
            "orphans": list(self.orphans),
            "warnings": list(self.warnings),
            "launchers": [dict(item) for item in self.launchers],
            "bindings_note": self.bindings_note,
            "path_written": False,
            "bindings_changed": False,
            "files_deleted": 0,
            "reason_code": self.reason_code(),
        }
        _require_self_consistent(document)
        return document


#: The one pairing this report face has: a status word per reason code. `search-response` uses the same
#: two words for the same two tiers, so a reader does not have to learn a third vocabulary.
STATUS_FOR_REASON = {"SUCCESS": "ok", "PATH_EXPOSURE_VIOLATION": "degraded"}


def _require_self_consistent(document: dict[str, Any]) -> None:
    """The report's own consistency rule, checked before it can be printed.

    There is no published schema for this report face — `path verify`'s document has none either; both
    are CLI reports, not contract documents — so `schema_io.validate_self` has nothing to check against.
    What *can* be checked without a schema is checked:

    * the reason code is one this report may carry, and the status word is the one that pairs with it;
    * every expected capability is accounted for by exactly one outcome list (missing from all three is
      a write this verb forgot to report; present in two of them is a contradiction);
    * ``orphans`` and ``expected`` are disjoint, and the reason code agrees with whether there are any —
      a document that named an orphan and still said `SUCCESS` would be `path verify`'s contradiction
      rebuilt inside this verb;
    * nothing was deleted: this verb has no delete path.

    Either kind of break is an implementation defect, which is what `SELF_VALIDATION_FAILED` means.
    """

    problems: list[str] = []
    code = str(document.get("reason_code"))
    if code not in STATUS_FOR_REASON:
        problems.append(
            f"reason_code {code!r} is not one a repair report may carry ({sorted(STATUS_FOR_REASON)})"
        )
    elif document.get("status") != STATUS_FOR_REASON[code]:
        problems.append(
            f"reason_code {code} pairs with status {STATUS_FOR_REASON[code]!r}, not "
            f"{document.get('status')!r}"
        )
    accounted = [*document["created"], *document["rewritten"], *document["already_present"]]
    if len(set(accounted)) != len(accounted) or sorted(accounted) != sorted(document["expected"]):
        problems.append(
            f"expected={document['expected']} but the outcome lists account for {sorted(accounted)}"
        )
    if document["repaired"] != sorted(set(document["created"]) | set(document["rewritten"])):
        problems.append(f"repaired={document['repaired']} is not exactly created ∪ rewritten")
    overlapping = sorted(set(document["orphans"]) & set(document["expected"]))
    if overlapping:
        problems.append(f"these capabilities are both expected and orphaned: {overlapping}")
    if bool(document["orphans"]) != (code == "PATH_EXPOSURE_VIOLATION"):
        problems.append(f"orphans={document['orphans']} and reason_code={code} tell different stories")
    if document["files_deleted"] != 0:
        problems.append("this verb has no delete path; files_deleted must stay 0")
    if problems:
        raise AirootError(
            "SELF_VALIDATION_FAILED",
            "the path repair report disagrees with itself",
            evidence=problems,
        )


def repair_path_exposure(
    root: Path,
    *,
    expected_launchers: list[str] | tuple[str, ...] = (),
    bindings_note: str | None = None,
) -> PathRepair:
    """Write the stable entries this root's active machine-level bindings are missing (ADR-0050).

    The **write half** of the exposure surface, and only that half:

    * it writes ``cli/exposure/bin/<capability>.cmd`` through the same `write_launcher` the ``EXPOSED``
      step of both runners calls, so a repaired entry is byte-identical to an installed one. There is
      no second renderer here, and no second answer to "which entries should exist" — the caller passes
      ``expected_launchers``, which `caps.lifecycle.expected_launchers` derives and `path verify` reads;
    * it rewrites an entry whose bytes are not what this build writes (a hand edit, or a moved
      interpreter/checkout): that is the *other* drift `path verify` reports, and the write is the same
      idempotent one;
    * it leaves an entry whose bytes already match **untouched, byte for byte**, and reports that as
      ``already_present`` rather than as a no-op it cannot describe;
    * it **never deletes** anything (deleting root-internal objects belongs to a plan plus an approval,
      规划 §9.6), never writes PATH, never changes a binding, and needs no approval: this file is not
      protected state (ADR-0050 measured that the same user can write it). An entry that exists and that
      **no** active machine binding projects is therefore *reported*, not removed — as ``orphans``,
      with `PATH_EXPOSURE_VIOLATION` (exit 2) and a warning saying why, because the alternative is this
      verb saying `SUCCESS` about a root `path verify` calls drifting (draft §165 ruling).

    An unreadable registry is a **refusal**, not an empty expectation. This version only writes, so it
    cannot make such a root worse — but "already consistent" is a claim about a question it could not
    ask, and `REGISTRY_MISSING` (exit 6) with the caller's note in ``evidence`` is the honest answer
    (draft §165).
    """

    root = Path(root)
    if bindings_note is not None:
        raise AirootError(
            "REGISTRY_MISSING",
            "the stable entries this root should have cannot be derived from an unreadable registry, "
            "so none may be written",
            evidence=[
                bindings_note,
                "an unknown expectation is not an empty one: `path repair` writes and never deletes, "
                "but it must not report a root it cannot read as already consistent",
                "`path verify` still answers on this root: it carries the same fact as an info finding",
            ],
        )

    expected = sorted(set(str(item) for item in expected_launchers))
    repair = PathRepair(root=str(root), expected=expected)
    for capability_id in expected:
        written = write_launcher(root, capability_id)
        repair.launchers.append(written.to_document())
        if written.created:
            repair.created.append(capability_id)
        elif written.rewritten:
            repair.rewritten.append(capability_id)
        else:
            repair.already_present.append(capability_id)
    repair.repaired = [
        capability_id
        for capability_id in expected
        if capability_id in set(repair.created) | set(repair.rewritten)
    ]
    # The same derivation as `expected`, read off the disk instead of the registry: every `*.cmd` in the
    # sanctioned directory that no active machine-level binding projects. `discover_launchers` is the
    # one reader of that directory (the one `path verify` uses), so this cannot see a different set.
    present = sorted({str(item.capability_id) for item in discover_launchers(root)})
    repair.orphans = sorted(set(present) - set(expected))
    if repair.orphans:
        repair.warnings.append(
            f"these stable entries have no active machine-level binding: {', '.join(repair.orphans)}. "
            "`path repair` only writes — removing a file inside the root is a plan plus an approval "
            "(规划 §9.6) — so they were left exactly as they are, and `path verify` reports the same "
            "drift. An entry an earlier `retire` left behind is cleared by running `tool retire` again "
            "for that capability's instance (its idempotent path re-checks the projection); anything "
            "else is a file somebody wrote by hand, and removing it is a manual step."
        )
    return repair


__all__ = [
    "PathFinding",
    "PathRepair",
    "PathVerification",
    "SANCTIONED_RELATIVE",
    "repair_path_exposure",
    "sanctioned_entry",
    "verify_path_exposure",
]
