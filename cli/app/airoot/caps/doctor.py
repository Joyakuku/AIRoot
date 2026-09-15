"""``doctor``: D1-D10 invariants with stable codes, evidence and remediation.

The verification plan references invariants "D1-D10" without ever defining them;
this module *is* that definition (see ``docs/AIROOT-v0.3-诊断码与ReasonCode表.md``):

D1 root identity   D2 registry integrity   D3 declared/physical payload
D4 orphan objects  D5 binding integrity    D6 transaction journal
D7 JSON projection D8 extension manifests  D9 security-mode honesty
D10 audit projection

``doctor`` reports and never deletes: unfamiliar objects become unmanaged/orphaned
findings, never automatic cleanup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import ENV_DIR, LOGS_DIR, TOOLS_DIR
from ..canon import digest_file, tree_digest
from ..clock import Clock, SYSTEM_CLOCK
from ..exits import AirootError
from ..paths import from_root_relative
from ..registry import Registry
from ..registry.entities import is_store_path, load_json
from ..registry.projection import projection_generation
from ..root import marker_path, read_marker
from ..schema_io import errors_for, load_schema
from ..paths import volume_serial
from .acl import AclSnapshot, acl_digest, capture_acl, differences

AUDIT_RELATIVE = f"{LOGS_DIR}/audit/events.json"

# The D1-D10 invariant catalogue referenced by the verification plan §8 but never
# defined by it. Every diagnostic code the core can emit must appear here.
INVARIANTS: dict[str, tuple[str, ...]] = {
    # D1 is extended by draft §9: not only the body root's identity must be provable, but every
    # registered data root's too. A data root whose path or volume moved invalidates every
    # reference recorded under it.
    "D1": (
        "ROOT_MARKER_MISSING",
        "ROOT_MARKER_INVALID",
        "VOLUME_IDENTITY_MISMATCH",
        "DATA_ROOT_MISSING",
        "DATA_ROOT_VOLUME_MISMATCH",
        "DATA_ROOT_ACL_DRIFT",
    ),
    "D2": ("REGISTRY_MISSING", "REGISTRY_INTEGRITY_FAILED", "SCHEMA_UNSUPPORTED"),
    # D3 distinguishes the two ownership domains (draft §9): an owned payload has an install-time
    # digest baseline (`PAYLOAD_MISSING` / `MANIFEST_DIGEST_MISMATCH`), a steward reference has
    # only observation, so its comparison is `REFERENCE_STALE` / `REFERENCE_DRIFTED` /
    # `REFERENCE_UNPROBED`.
    "D3": (
        "PAYLOAD_MISSING",
        "MANIFEST_DIGEST_MISMATCH",
        "REFERENCE_STALE",
        "REFERENCE_DRIFTED",
        "REFERENCE_UNPROBED",
        "WHITELIST_REVISION_STALE",
    ),
    "D4": ("ORPHANED_STORE_INSTANCE", "UNMANAGED_OBJECT_PRESENT", "PAYLOAD_OUTSIDE_STORE"),
    "D5": ("BINDING_TARGET_MISSING", "MULTIPLE_ACTIVE_BINDINGS", "DESIRED_NOT_SATISFIED"),
    "D6": ("PENDING_TRANSACTION", "RECOVERY_REQUIRED", "JOURNAL_TRUNCATED"),
    "D7": ("REGISTRY_PROJECTION_STALE", "SEARCH_INDEX_DEGRADED", "SEARCH_RESULT_STALE"),
    "D8": ("EXTENSION_MANIFEST_INVALID", "EXTENSION_VERSION_UNSUPPORTED"),
    "D9": ("POLICY_ONLY_MODE",),
    "D10": ("AUDIT_PROJECTION_DRIFT",),
}

DIAGNOSTIC_CODES = frozenset(code for codes in INVARIANTS.values() for code in codes)

SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _diagnostic(
    severity: str,
    code: str,
    evidence: list[str],
    impact: str,
    remediation: str,
    capability: str | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "capability": capability,
        "evidence": evidence or ["no structured evidence available"],
        "impact": impact,
        "remediation": remediation,
    }


def _check_root(root: Path, diagnostics: list[dict[str, Any]]) -> dict[str, Any] | None:
    """D1: root identity must be provable, not assumed."""

    path = marker_path(root)
    if not path.is_file():
        diagnostics.append(
            _diagnostic(
                "critical",
                "ROOT_MARKER_MISSING",
                [str(path)],
                "the root cannot be trusted or selected",
                "recover",
            )
        )
        return None
    try:
        marker = read_marker(root)
    except AirootError as error:
        diagnostics.append(
            _diagnostic("critical", error.reason_code, [error.message, *error.evidence], "root identity unknown", "recover")
        )
        return None
    try:
        actual = volume_serial(root)
    except AirootError as error:
        diagnostics.append(
            _diagnostic("critical", error.reason_code, [error.message], "volume identity unreadable", "recover")
        )
        return marker
    if actual != marker["volume_serial"]:
        diagnostics.append(
            _diagnostic(
                "critical",
                "VOLUME_IDENTITY_MISMATCH",
                [f"marker={marker['volume_serial']}", f"actual={actual}"],
                "the directory is not the volume this registry was created on",
                "recover",
            )
        )
    return marker


def _check_data_root_acl(
    data_root_id: str, path: Path, row: Any, diagnostics: list[dict[str, Any]]
) -> None:
    """Compare the data root's ACL with the baseline recorded when it was registered (ADR-0023).

    Three outcomes, and the distinction between them is the whole point:

    * **no baseline** — nothing to compare, so nothing is claimed. That is what "P2 之前不发" means
      precisely: before this stage existed there was never a baseline.
    * **either side unobserved** — the baseline cannot be *confirmed*, which leaves D1 ("this data
      root's identity is provable") unmet. Reported under the same code, with evidence that says
      which side could not be read, because "unreadable" and "changed" are different facts and the
      answer must not blur them. This follows this file's own convention for volume identity
      ("volume identity unreadable" is reported as ``VOLUME_IDENTITY_MISMATCH``).
    * **both observed and different** — drift, with the differences named.

    ``remediation`` is ``repair`` as the steward draft's table says, and in this project's vocabulary
    that means **re-record the baseline once the change is understood** — the same reading
    ``WHITELIST_REVISION_STALE`` gets. It is deliberately *not* "put the old ACL back": that would
    undo a change the user may have made on purpose, which the butler model forbids (ADR-0004).
    """

    recorded_document = row["acl_baseline_json"]
    recorded = load_json(recorded_document, None) if isinstance(recorded_document, str) else recorded_document
    if not isinstance(recorded, dict):
        return  # registered before baselines were recorded: no comparison is possible

    expected = AclSnapshot.from_document(str(path), recorded)
    current = capture_acl(path)
    common = [f"data_root={data_root_id}", f"path={path}"]

    if not expected.observed or not current.observed:
        unreadable = "baseline" if not expected.observed else "current"
        diagnostics.append(
            _diagnostic(
                "warning",
                "DATA_ROOT_ACL_DRIFT",
                [
                    *common,
                    f"{unreadable} could not be read: {expected.reason or current.reason}",
                    "this is an unreadable ACL, not a changed one",
                ],
                "the data root's ACL cannot be confirmed against the recorded baseline",
                "repair",
            )
        )
        return

    findings = differences(expected, current)
    if not findings:
        return
    diagnostics.append(
        _diagnostic(
            "warning",
            "DATA_ROOT_ACL_DRIFT",
            [
                *common,
                f"recorded={acl_digest(expected)[:19]}",
                f"actual={acl_digest(current)[:19]}",
                *findings[:4],
            ],
            "the sharing posture of this data root changed since it was registered",
            "repair",
        )
    )


def _check_registry(
    root: Path, registry: Registry | None, diagnostics: list[dict[str, Any]]
) -> Registry | None:
    """D2: the declared state must be readable and self-consistent."""

    if registry is None:
        try:
            registry = Registry.open(root)
        except AirootError as error:
            severity = "critical" if error.reason_code in {"REGISTRY_INTEGRITY_FAILED"} else "error"
            remediation = "rebuild" if error.reason_code == "REGISTRY_INTEGRITY_FAILED" else "recover"
            diagnostics.append(
                _diagnostic(severity, error.reason_code, [error.message, *error.evidence], "declared state unreadable", remediation)
            )
            return None
    try:
        if registry.schema_version != 1:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "SCHEMA_UNSUPPORTED",
                    [f"registry schema_version={registry.schema_version}"],
                    "this build cannot read the registry",
                    "none",
                )
            )
    except AirootError as error:
        diagnostics.append(_diagnostic("critical", error.reason_code, [error.message], "registry metadata unreadable", "rebuild"))
        return None

    for problem in registry.integrity_problems():
        code = "REGISTRY_INTEGRITY_FAILED"
        if "multiple active bindings" in problem:
            code = "MULTIPLE_ACTIVE_BINDINGS"
        elif "missing instance" in problem:
            code = "BINDING_TARGET_MISSING"
        diagnostics.append(
            _diagnostic("error", code, [problem], "declared state is inconsistent", "repair")
        )
    return registry


def _check_payloads(registry: Registry, root: Path, verify: bool, diagnostics: list[dict[str, Any]]) -> None:
    """D3: declared instances must exist on disk, and optionally match their digest."""

    for row in registry.instances():
        instance_id = str(row["instance_id"])
        if row["collected_at"]:
            # Deliberately collected: the row is kept for binding history, the payload is gone
            # by an approved decision. That is not a defect (draft §19.3-2).
            continue
        try:
            store_dir = from_root_relative(str(row["store_path"]), root)
        except AirootError as error:
            diagnostics.append(
                _diagnostic("error", error.reason_code, [error.message], f"{instance_id} payload unreachable", "repair", instance_id)
            )
            continue
        if not store_dir.is_dir():
            diagnostics.append(
                _diagnostic(
                    "error",
                    "PAYLOAD_MISSING",
                    [f"instance={instance_id}", f"store_path={row['store_path']}"],
                    "the instance is declared but its payload is gone",
                    "rebuild",
                    instance_id,
                )
            )
            continue
        if verify:
            actual = tree_digest(store_dir)
            if actual != row["artifact_digest"]:
                diagnostics.append(
                    _diagnostic(
                        "error",
                        "MANIFEST_DIGEST_MISMATCH",
                        [f"instance={instance_id}", f"declared={row['artifact_digest']}", f"actual={actual}"],
                        "the payload changed after installation",
                        "repair",
                        instance_id,
                    )
                )


def _check_orphans(registry: Registry, root: Path, diagnostics: list[dict[str, Any]]) -> None:
    """D4: payloads inside the root with no registry record are reported, never adopted."""

    store = root / "store"
    if not store.is_dir():
        return
    declared = {str(row["store_path"]).replace("\\", "/") for row in registry.instances()}
    for entry in sorted(path for path in store.iterdir() if path.is_dir()):
        for leaf in sorted(path for path in entry.rglob("*") if path.is_dir() and (path / "artifact.json").is_file()):
            relative = leaf.relative_to(root).as_posix()
            if relative not in declared:
                diagnostics.append(
                    _diagnostic(
                        "warning",
                        "ORPHANED_STORE_INSTANCE",
                        [f"store object without a registry row: {relative}"],
                        "the object is unmanaged; where will not select it",
                        "inspect",
                    )
                )


def _check_layout(registry: Registry, root: Path, diagnostics: list[dict[str, Any]]) -> None:
    """D4: an owned payload belongs in ``store/`` and nowhere else (draft §66).

    Two shapes, one invariant, and the same code for both because the reader's next move is the same
    ("move it into the store, or stop declaring it"):

    * a **declared** instance whose ``store_path`` points outside ``store/`` — `error`, because that
      declaration cannot be honoured; `where` refuses to activate it;
    * a payload **marker** under a view directory (``tools/``, ``env/``) with nobody declaring it —
      `warning`, because nothing is unusable, the layout merely drifted.

    Reported unconditionally, unlike ``UNMANAGED_OBJECT_PRESENT`` (which is behind
    ``--include-unmanaged``): an unfamiliar object in a data root is information, while a payload in a
    view directory contradicts a frozen contract. Gating the second one behind a flag would let the
    quiet default hide a violated invariant.
    """

    for row in registry.instances():
        if is_store_path(row["store_path"]):
            continue
        diagnostics.append(
            _diagnostic(
                "error",
                "PAYLOAD_OUTSIDE_STORE",
                [
                    f"instance={row['instance_id']}",
                    f"store_path={row['store_path']}",
                    "the declared payload path is not under store/",
                ],
                "the declaration cannot be honoured; where will not select this payload",
                "repair",
                str(row["instance_id"]),
            )
        )

    declared = {str(row["store_path"]).replace("\\", "/") for row in registry.instances()}
    for view in (TOOLS_DIR, ENV_DIR):
        directory = root / view
        if not directory.is_dir():
            continue
        for leaf in sorted(path for path in directory.rglob("*") if path.is_dir() and (path / "artifact.json").is_file()):
            relative = leaf.relative_to(root).as_posix()
            if relative in declared:
                # Already reported above as a mislocated declaration; one fact, one diagnostic.
                continue
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "PAYLOAD_OUTSIDE_STORE",
                    [f"payload marker outside the store: {relative}", f"{view}/ is a binding/view directory and carries no payload"],
                    "layout drifted from the frozen rule that store is the only payload storage",
                    "inspect",
                )
            )


def _check_data_roots(
    registry: Registry,
    diagnostics: list[dict[str, Any]],
    *,
    whitelist_revision: str | None = None,
) -> None:
    """D1 extension (draft §9): every registered data root's identity must be provable.

    The root marker proves the *body*; a data root is an external promise. If its path is gone
    or its volume changed, every reference recorded under it describes something else now — and
    ``doctor`` must say so rather than let ``where`` keep answering from stale facts.
    """

    for row in registry.data_roots(active_only=True):
        data_root_id = str(row["data_root_id"])
        path = Path(str(row["path"]))
        if not path.is_dir():
            diagnostics.append(
                _diagnostic(
                    "error",
                    "DATA_ROOT_MISSING",
                    [f"data_root={data_root_id}", f"path={path}"],
                    "every reference recorded under this data root is unverifiable",
                    "inspect",
                )
            )
            continue
        try:
            actual = volume_serial(path)
        except AirootError as error:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "DATA_ROOT_VOLUME_MISMATCH",
                    [f"data_root={data_root_id}", f"path={path}", error.message],
                    "the data root's volume identity is unreadable",
                    "recover",
                )
            )
            continue
        recorded = str(row["volume_serial"])
        if actual != recorded:
            diagnostics.append(
                _diagnostic(
                    "error",
                    "DATA_ROOT_VOLUME_MISMATCH",
                    [f"data_root={data_root_id}", f"recorded={recorded}", f"actual={actual}", f"path={path}"],
                    "the path now resolves to a different volume than when it was registered",
                    "recover",
                )
            )
        _check_data_root_acl(data_root_id, path, row, diagnostics)
        stale_revision = str(row["whitelist_revision"] or "")
        if whitelist_revision and stale_revision and stale_revision != whitelist_revision:
            diagnostics.append(
                _diagnostic(
                    "info",
                    "WHITELIST_REVISION_STALE",
                    [
                        f"data_root={data_root_id}",
                        f"recorded={stale_revision}",
                        f"current={whitelist_revision}",
                    ],
                    "observations were made with an older capability whitelist; re-run discover to refresh",
                    "repair",
                )
            )


def _check_references(
    registry: Registry,
    root: Path,
    verify: bool,
    include_unmanaged: bool,
    diagnostics: list[dict[str, Any]],
    *,
    whitelist: Any = None,
) -> None:
    """D3 steward branch (draft §9): compare a reference against a fresh read-only observation.

    There is no install-time baseline for an object AIROOT never installed, so drift can only be
    *observed*. The re-observation is bounded (PE header and resource section only): reading a
    120 MB ``node.exe`` in full on every ``doctor`` run is exactly what the bounded probe exists
    to avoid. Full-file digests are opt-in through ``verify``, matching owned payloads.
    """

    from .discovery import classify_object, load_whitelist

    rows = registry.external_references()
    if not rows:
        return
    try:
        rules = whitelist if whitelist is not None else load_whitelist()
    except AirootError as error:
        diagnostics.append(
            _diagnostic(
                "info",
                "REFERENCE_UNPROBED",
                [f"the capability whitelist is unreadable: {error.message}"],
                "references cannot be re-observed, so drift cannot be detected",
                "repair",
            )
        )
        return

    for row in rows:
        external_id = str(row["external_id"])
        management = str(row["management"])
        path = Path(str(row["path"]))
        if management != "external_reference":
            if include_unmanaged:
                diagnostics.append(
                    _diagnostic(
                        "info",
                        "UNMANAGED_OBJECT_PRESENT",
                        [f"external_id={external_id}", f"management={management}", f"path={path}"],
                        "an object that matches no frozen capability was observed; it is reported, never adopted",
                        "none",
                    )
                )
            continue
        if not path.exists():
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "REFERENCE_STALE",
                    [f"external_id={external_id}", f"path={path}", "the referenced object is gone"],
                    "where must not resolve this capability through a missing object",
                    "inspect",
                )
            )
            continue

        version = str(row["active_version"] or row["version"] or "")
        probe_level = row["probe_level"]
        if not version or version == "unknown" or not probe_level:
            diagnostics.append(
                _diagnostic(
                    "info",
                    "REFERENCE_UNPROBED",
                    [
                        f"external_id={external_id}",
                        f"version={version or 'unknown'}",
                        f"probe_level={probe_level}",
                    ],
                    "only the path is known; a version constraint cannot be proven against it",
                    "inspect",
                )
            )

        observed = classify_object(path, data_root_id=row["data_root_id"], whitelist=rules)
        differences: list[str] = []
        if observed.management != "external_reference":
            differences.append(
                f"the object no longer matches a whitelisted capability (now {observed.management})"
            )
        if observed.version and version and observed.version != version:
            differences.append(f"version {version} -> {observed.version}")
        if observed.active_version != row["active_version"]:
            differences.append(f"active_version {row['active_version']} -> {observed.active_version}")
        recorded_versions = [str(item) for item in load_json(row["versions_json"], [])]
        if recorded_versions and list(observed.versions) != recorded_versions:
            differences.append(f"versions {recorded_versions} -> {list(observed.versions)}")
        if observed.architecture and row["architecture"] and observed.architecture != row["architecture"]:
            differences.append(f"architecture {row['architecture']} -> {observed.architecture}")
        recorded_entrypoints = [str(item) for item in load_json(row["entrypoints_json"], [])]
        if recorded_entrypoints and list(observed.entrypoints) != recorded_entrypoints:
            differences.append(f"entrypoints {recorded_entrypoints} -> {list(observed.entrypoints)}")
        if differences:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "REFERENCE_DRIFTED",
                    [f"external_id={external_id}", f"path={path}", *differences],
                    "the observed facts no longer match what was recorded; nothing is repaired automatically",
                    "inspect",
                )
            )

        if verify:
            entrypoint = (Path(str(row["path"])) / recorded_entrypoints[0]) if recorded_entrypoints else None
            recorded_digest = row["observed_digest"]
            if entrypoint is not None and entrypoint.is_file() and recorded_digest:
                actual = digest_file(entrypoint)
                if actual != recorded_digest:
                    diagnostics.append(
                        _diagnostic(
                            "warning",
                            "REFERENCE_DRIFTED",
                            [
                                f"external_id={external_id}",
                                f"entrypoint={entrypoint}",
                                f"recorded={recorded_digest}",
                                f"actual={actual}",
                            ],
                            "the entrypoint's contents changed after it was observed",
                            "inspect",
                        )
                    )


def _check_desired(registry: Registry, root: Path, diagnostics: list[dict[str, Any]]) -> None:
    """D5: desired (intent) vs declared (active binding) — draft §28.

    Deliberately separate from drift: ``DRIFT_DETECTED`` means observation disagrees with
    declaration, this means **intent** disagrees with declaration, and the user's next move is
    different in each case (fix the environment vs change the manifest).

    A machine with no manifest produces no finding at all, so `doctor` output stays byte-stable
    for roots that never pinned anything.
    """

    from .desired import evaluate, load_desired

    path = Path(root) / "state" / "desired.json"
    if not path.is_file():
        return
    try:
        manifest = load_desired(root)
    except AirootError as error:
        # A diagnostic tool must answer for bad input rather than raise: we cannot tell whether
        # intent is satisfied, which is the same kind of uncertainty as "it is not".
        diagnostics.append(
            _diagnostic(
                "warning",
                "DESIRED_NOT_SATISFIED",
                [f"the desired manifest is unreadable: {error.message}", *error.evidence[:3]],
                "whether your pinned intents are satisfied cannot be determined",
                "inspect",
            )
        )
        return
    for entry in evaluate(registry, manifest):
        if entry.in_sync:
            continue
        diagnostics.append(
            _diagnostic(
                "warning",
                "DESIRED_NOT_SATISFIED",
                [
                    f"capability={entry.capability_id}",
                    f"desired={entry.desired_version or '*'}",
                    f"active={entry.active_version or 'nothing'}",
                    entry.detail,
                ],
                "the declared state does not satisfy the pin; nothing is changed automatically",
                "inspect",
                entry.capability_id,
            )
        )


def _check_transactions(registry: Registry, diagnostics: list[dict[str, Any]]) -> None:
    """D6: an unfinished transaction must be explained, never guessed."""

    for row in registry.transactions(unfinished_only=True):
        state = str(row["state"])
        payload = json.loads(row["payload_json"])
        if state == "RECOVERY_REQUIRED":
            diagnostics.append(
                _diagnostic(
                    "error",
                    "RECOVERY_REQUIRED",
                    [f"transaction={row['transaction_id']}", "state=RECOVERY_REQUIRED", f"journal_seq={row['journal_seq']}"],
                    "a controlled change stopped with ambiguous evidence",
                    "recover",
                )
            )
        else:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "PENDING_TRANSACTION",
                    [f"transaction={row['transaction_id']}", f"state={state}", f"journal_seq={row['journal_seq']}"],
                    "a controlled change did not reach a terminal state",
                    "recover",
                )
            )
        if not payload.get("journal_seq"):
            diagnostics.append(
                _diagnostic(
                    "error",
                    "JOURNAL_TRUNCATED",
                    [f"transaction={row['transaction_id']}"],
                    "the journal has no usable sequence",
                    "recover",
                )
            )


def _check_projection(registry: Registry, root: Path, diagnostics: list[dict[str, Any]]) -> None:
    """D7: the JSON projection is derived; drift is reported, not silently repaired."""

    generation = projection_generation(root)
    if generation is None:
        diagnostics.append(
            _diagnostic(
                "warning",
                "REGISTRY_PROJECTION_STALE",
                ["state/registry.json is missing or unreadable"],
                "agents reading the projection see no declared state",
                "rebuild",
            )
        )
    elif generation != registry.generation:
        diagnostics.append(
            _diagnostic(
                "warning",
                "REGISTRY_PROJECTION_STALE",
                [f"projection generation={generation}", f"registry generation={registry.generation}"],
                "the projection does not describe the current generation",
                "rebuild",
            )
        )


def _check_search_index(root: Path, diagnostics: list[dict[str, Any]], *, clock: Clock) -> None:
    """D7: the crawl-built search index is derived state too (draft §33).

    Three deliberate omissions keep this from becoming noise:

    * **no index at all is not a problem** — a machine that never searched has nothing to report,
      and a diagnostic that fires on every such machine is the kind of noise that makes the severe
      ones ignorable;
    * `doctor` never builds one: a full walk is a side effect, and a diagnostic tool only reports;
    * a **fresh** index produces nothing, so the healthy case stays byte-identical.
    """

    from . import searchindex
    from .search import load_search_policy

    policy = load_search_policy()
    state = searchindex.read_state(root, policy=policy)
    if not state.present:
        return
    if not state.readable:
        diagnostics.append(
            _diagnostic(
                "warning",
                "SEARCH_INDEX_DEGRADED",
                [
                    f"the search index is unusable: {state.problem}",
                    f"path={searchindex.index_path(root, policy)}",
                    "a live crawl still answers queries, so results are not lost",
                ],
                "indexed searches are answered by a live crawl until it is rebuilt",
                "rebuild",
            )
        )
        return

    fresh, stale_reason = searchindex.freshness_for(
        state,
        {"max_staleness_ms": int(policy.index_setting("max_age_ms", 86_400_000))},
        clock=clock,
    )
    if stale_reason == "SEARCH_RESULT_STALE":
        diagnostics.append(
            _diagnostic(
                "warning",
                "SEARCH_RESULT_STALE",
                [
                    f"index built_at={state.built_at}",
                    f"lag_ms={fresh['lag_ms']} exceeds index.max_age_ms="
                    f"{policy.index_setting('max_age_ms', 86_400_000)}",
                    f"records={state.records} coverage={state.coverage}",
                    "rebuild it with: airoot search refresh",
                ],
                "indexed search answers describe an older listing than the policy allows",
                "rebuild",
            )
        )


def _check_extensions(registry: Registry, diagnostics: list[dict[str, Any]]) -> None:
    """D8: an extension manifest must satisfy the published contract."""

    schema = load_schema("extension-manifest")
    for row in registry.extensions():
        manifest = json.loads(row["manifest_json"])
        problems = errors_for("extension-manifest", manifest)
        if problems:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "EXTENSION_MANIFEST_INVALID",
                    [f"extension={row['extension_id']}", *problems[:4]],
                    "the extension cannot be trusted to run",
                    "inspect",
                )
            )
        if int(manifest.get("protocol_version", 0)) != schema["properties"]["protocol_version"].get("const", 1):
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "EXTENSION_VERSION_UNSUPPORTED",
                    [f"extension={row['extension_id']}", f"protocol_version={manifest.get('protocol_version')}"],
                    "the extension protocol major version is not supported",
                    "inspect",
                )
            )


def _check_audit(registry: Registry, root: Path, diagnostics: list[dict[str, Any]]) -> None:
    """D10: ``logs/audit`` is rebuildable; divergence must be reported."""

    from ..registry.projection import audit_projection

    expected = audit_projection(registry)
    path = root / AUDIT_RELATIVE
    if not path.is_file():
        if expected["event_count"]:
            diagnostics.append(
                _diagnostic(
                    "warning",
                    "AUDIT_PROJECTION_DRIFT",
                    [f"missing {AUDIT_RELATIVE}", f"authoritative events={expected['event_count']}"],
                    "the audit projection can be rebuilt from state/events",
                    "rebuild",
                )
            )
        return
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        diagnostics.append(
            _diagnostic("warning", "AUDIT_PROJECTION_DRIFT", [str(error)], "the audit projection is unreadable", "rebuild")
        )
        return
    if actual.get("digest") != expected["digest"]:
        diagnostics.append(
            _diagnostic(
                "warning",
                "AUDIT_PROJECTION_DRIFT",
                [f"projection digest={actual.get('digest')}", f"event digest={expected['digest']}"],
                "the audit projection is not derived from the current events",
                "rebuild",
            )
        )


def doctor(
    root: Path,
    *,
    clock: Clock = SYSTEM_CLOCK,
    verify: bool = False,
    registry: Registry | None = None,
    security_mode: str = "policy_only",
    include_unmanaged: bool = False,
    data_roots: bool = True,
) -> dict[str, Any]:
    """Run every invariant and return a schema-valid diagnosis.

    ``data_roots=False`` is used when checking a body root that has no steward domain at all
    (and by tests that must not depend on the machine's real data roots).
    """

    root = Path(root)
    diagnostics: list[dict[str, Any]] = []

    marker = _check_root(root, diagnostics)
    registry = _check_registry(root, registry, diagnostics)
    if registry is not None:
        _check_payloads(registry, root, verify, diagnostics)
        _check_orphans(registry, root, diagnostics)
        _check_layout(registry, root, diagnostics)
        if data_roots:
            whitelist_revision = None
            try:
                from .discovery import load_whitelist

                whitelist_revision = load_whitelist().revision
            except AirootError:
                whitelist_revision = None
            _check_data_roots(registry, diagnostics, whitelist_revision=whitelist_revision)
            _check_references(
                registry, root, verify, include_unmanaged, diagnostics
            )
        _check_transactions(registry, diagnostics)
        _check_desired(registry, root, diagnostics)
        _check_projection(registry, root, diagnostics)
        _check_search_index(root, diagnostics, clock=clock)
        _check_extensions(registry, diagnostics)
        _check_audit(registry, root, diagnostics)

    diagnostics.append(
        _diagnostic(
            "info",
            "POLICY_ONLY_MODE",
            ["P1 has no ACL, broker or machine PATH exposure"],
            "enforcement is convention and audit only; a same-user process can bypass it",
            "none",
        )
    )

    status = _status_for(diagnostics)
    document: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "root_instance_id": str(marker["root_instance_id"]) if marker else "unknown",
        "registry_generation": registry.generation if registry is not None else 0,
        "diagnostics": diagnostics,
        "checked_at": clock.timestamp(),
        "security_mode": security_mode,
        "enforcement": "acl_enforced" if security_mode == "protected_machine" else "same_user_can_bypass",
    }
    from ..schema_io import validate_self

    validate_self("doctor-response", document)
    return document


def _status_for(diagnostics: list[dict[str, Any]]) -> str:
    codes = {str(item["code"]) for item in diagnostics}
    severities = {str(item["severity"]) for item in diagnostics}
    if codes & {"ROOT_MARKER_MISSING", "ROOT_MARKER_INVALID", "VOLUME_IDENTITY_MISMATCH", "RECOVERY_REQUIRED", "JOURNAL_TRUNCATED"}:
        return "recovery_required"
    if "error" in severities or "critical" in severities:
        return "broken"
    if "warning" in severities:
        return "degraded"
    return "healthy"


def status_exit_code(status: str) -> int:
    """Exit codes for a doctor run, decided by the command's final state (v0.3 §15.6)."""

    from ..exits import EXIT_BROKEN, EXIT_DEGRADED, EXIT_RECOVERY, EXIT_SUCCESS

    return {
        "healthy": EXIT_SUCCESS,
        "degraded": EXIT_DEGRADED,
        "broken": EXIT_BROKEN,
        "recovery_required": EXIT_RECOVERY,
    }[status]


def diagnostics_by_code(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["code"]): item for item in document["diagnostics"]}
