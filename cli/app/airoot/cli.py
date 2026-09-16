"""The ``airoot`` command line.

Only documented commands are exposed (v0.3 §15.1-§15.5). Text output is a rendering
of the JSON document; the JSON, the reason codes and the exit codes are the
contract. P1 never writes machine PATH, ACL or anything outside the resolved root.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import PROTOCOL_VERSION
from .caps.doctor import doctor, status_exit_code
from .caps.inventory import inventory
from .caps.where import WhereQuery, where
from .clock import SYSTEM_CLOCK
from .exits import AirootError, EXIT_DEGRADED, EXIT_RECOVERY, EXIT_SUCCESS, exit_code_for
from .ext.fake import FakeExtension
from .ext.manifest import load_manifests
from .posture import SECURITY_MODE, enforcement_for
from .registry import Registry
from .root import open_root, resolve_root
from .schema_io import validate_document, validate_self
from .tx import create_plan, repair
from .tx.approval import load_keyring, record_approval, verify_approval
from .tx.simulate import SimulationRunner


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _emit(document: dict[str, Any], *, as_json: bool, lines: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(document, indent=2, sort_keys=True))
        return
    for line in lines or []:
        print(line)


def _reason_of(document: dict[str, Any], default: str = "SUCCESS") -> str:
    value = document.get("reason_code")
    return value if isinstance(value, str) else default


class Context:
    """Everything a command needs, resolved once from ``--root``.

    ``verify=False`` resolves the path without validating root identity, which is
    what ``doctor`` needs: a broken root must still be diagnosable.
    """

    def __init__(self, root_argument: str | None, *, verify: bool = True) -> None:
        self.clock = SYSTEM_CLOCK
        self.verify = verify
        if verify:
            self.root = open_root(root_argument)
            self.root_path: Path = self.root.path
        else:
            self.root = None
            self.root_path = resolve_root(root_argument)

    def registry(self) -> Registry:
        return Registry.open(self.root_path, clock=self.clock)

    def path(self) -> Path:
        return self.root_path


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_root_status(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "root_instance_id": context.root.root_instance_id if context.root else "unknown",
        "canonical_path": str(context.path()),
        "volume_serial": context.root.volume_serial if context.root else "unknown",
        "protocol_version": PROTOCOL_VERSION,
        "registry_generation": 0,
        "policy_revision": 0,
        "machine_id": "unknown",
        "registry_state": "available",
        "security_mode": SECURITY_MODE,
        "enforcement": enforcement_for(SECURITY_MODE),
        "reason_code": "SUCCESS",
    }
    code = EXIT_SUCCESS
    try:
        registry = context.registry()
    except AirootError as error:
        # The root identity is known but its declared state is not: report both.
        document["registry_state"] = "unavailable"
        document["reason_code"] = error.reason_code
        code = error.exit_code
    else:
        try:
            document["registry_generation"] = registry.generation
            document["policy_revision"] = registry.policy_revision
            document["machine_id"] = registry.machine_id
        finally:
            registry.close()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"root {document['canonical_path']} (generation {document['registry_generation']},"
            f" registry {document['registry_state']})"
        ],
    )
    return document, code


def cmd_where(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    registry = context.registry()
    try:
        document = where(
            registry,
            WhereQuery(
                capability_id=args.capability,
                version=args.version,
                scope=args.scope,
                session_id=args.session_id,
                project_id=args.project_id,
                allow_external_fallback=args.allow_external_fallback,
            ),
            root=context.path(),
        )
    finally:
        registry.close()
    lines = [
        f"{document['capability_id']}: found={document['found']} usable={document['usable']}"
        f" reason={document['reason_code']}"
    ]
    if document["executable"]:
        lines.append(f"  {document['executable']}")
        lines.append(
            f"  effective_now={document['effective_now']} effective_new_process={document['effective_new_process']}"
        )
    _emit(document, as_json=args.json, lines=lines)
    return document, exit_code_for(document["reason_code"])


def cmd_doctor(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    registry: Registry | None = None
    try:
        registry = context.registry()
    except AirootError:
        registry = None
    try:
        document = doctor(
            context.path(),
            clock=context.clock,
            verify=args.verify,
            registry=registry,
            security_mode=SECURITY_MODE,
            include_unmanaged=args.include_unmanaged,
        )
    finally:
        if registry is not None:
            registry.close()
    lines = [f"doctor: {document['status']}"]
    for item in document["diagnostics"]:
        lines.append(f"  [{item['severity']}] {item['code']}: {item['impact']} -> {item['remediation']}")
    _emit(document, as_json=args.json, lines=lines)
    return document, status_exit_code(document["status"])


def cmd_inventory(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    registry = context.registry()
    try:
        document = inventory(registry, scope=args.scope, klass=args.klass)
    finally:
        registry.close()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"inventory: {len(document['instances'])} instance(s), "
            f"{len(document['bindings'])} binding(s), "
            f"{len(document['external_references'])} external reference(s) at generation {document['generation']}"
        ],
    )
    return document, EXIT_SUCCESS


def _derived_data_root_id(path: Path) -> str:
    from .caps.discovery import slug

    return f"dr-{slug(path.name)}"


def cmd_data_root_add(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Register a data root: a directory AIROOT observes but never owns."""

    from .caps.discovery import load_whitelist
    from .paths import canonicalize, is_within, reject_reparse_chain, volume_serial
    from .registry import DataRoot

    registry = context.registry()
    try:
        target = canonicalize(args.path, must_exist=True)
        if not target.is_dir():
            raise AirootError("INVALID_INPUT", f"data root must be a directory: {target}")
        if is_within(target, context.path()):
            raise AirootError(
                "INVALID_INPUT",
                "a data root must live outside the CLI data root",
                evidence=[
                    f"cli root = {context.path()}",
                    "separating the body from the managed environment is the point (ADR-0004)",
                ],
            )
        reject_reparse_chain(target)
        # The data root's own volume identity is recorded so drift can be detected
        # later (DATA_ROOT_VOLUME_MISMATCH). It is explicitly **not** required to match
        # the CLI root's volume: nothing in the steward path moves a payload, and
        # same-volume atomicity only matters for the owned store, which lives inside
        # the CLI root by construction.
        target_volume = volume_serial(target)

        # The ACL is recorded as an **observation**, not a requirement (draft §3.1), so a later change
        # can be reported instead of silently accepted. Reading is all this does: imposing a baseline
        # needs WRITE_DAC behind the P2 broker (ADR-0023).
        from .caps.acl import capture_acl

        acl_baseline = capture_acl(target).to_document()

        data_root_id = args.data_root_id or _derived_data_root_id(target)
        existing = registry.data_root(data_root_id)
        if existing is not None and existing["path"] != str(target):
            raise AirootError(
                "INVALID_INPUT",
                f"data root id {data_root_id} is already registered for another path",
                evidence=[f"registered={existing['path']}", f"requested={target}"],
            )

        data_root = DataRoot(
            data_root_id=data_root_id,
            path=str(target),
            role=args.role,
            volume_serial=target_volume,
            acl_baseline=acl_baseline,
            added_at=context.clock.timestamp(),
            whitelist_revision=load_whitelist().revision,
        )
        with registry.write(expected_generation=registry.generation) as connection:
            registry.add_data_root(connection, data_root)
            registry.append_event(
                connection,
                state="DATA_ROOT_ADDED",
                detail=f"data root {data_root_id} -> {target} (role={args.role})",
                outcome="ok",
            )
        registry.update_projection()
    finally:
        registry.close()

    document = {
        "schema_version": 1,
        "data_root": data_root.to_schema(),
        "reason_code": "SUCCESS",
        "files_touched": 0,
    }
    _emit(
        document,
        as_json=args.json,
        lines=[f"data root {data_root_id} registered at {target} (role {args.role}); no files were written"],
    )
    return document, EXIT_SUCCESS


def cmd_data_root_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    from .registry.entities import data_root_from_row

    registry = context.registry()
    try:
        roots = [data_root_from_row(row).to_schema() for row in registry.data_roots(active_only=not args.all)]
    finally:
        registry.close()

    document = {"schema_version": 1, "data_roots": roots, "reason_code": "SUCCESS"}
    _emit(
        document,
        as_json=args.json,
        lines=[f"{item['data_root_id']}  {item['path']}  ({item['role']})" for item in roots] or ["no data roots registered"],
    )
    return document, EXIT_SUCCESS


def cmd_data_root_forget(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Unregister a data root and its observations. Files are never touched."""

    registry = context.registry()
    try:
        row = registry.data_root(args.data_root_id)
        if row is None:
            raise AirootError(
                "NOT_FOUND",
                f"unknown data root: {args.data_root_id}",
                evidence=[item["data_root_id"] for item in registry.data_roots(active_only=False)],
            )
        observed = registry.external_references_for_data_root(args.data_root_id)
        with registry.write(expected_generation=registry.generation) as connection:
            for reference in observed:
                registry.forget_external_reference(connection, reference["external_id"])
            registry.forget_data_root(connection, args.data_root_id)
            registry.append_event(
                connection,
                state="DATA_ROOT_FORGOTTEN",
                detail=f"data root {args.data_root_id} unregistered; {len(observed)} reference(s) dropped",
                outcome="ok",
            )
        registry.update_projection()
    finally:
        registry.close()

    document = {
        "schema_version": 1,
        "data_root_id": args.data_root_id,
        "references_dropped": len(observed),
        "files_touched": 0,
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[f"data root {args.data_root_id} unregistered ({len(observed)} reference(s) dropped); no files were touched"],
    )
    return document, EXIT_SUCCESS


def cmd_discover(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Read-only classification of registered data roots."""

    from .caps.discovery import discover_data_root, load_whitelist
    from .paths import volume_serial
    from .registry import ExternalReference
    from .registry.entities import data_root_from_row

    registry = context.registry()
    try:
        rules = load_whitelist()
        if args.data_root:
            row = registry.data_root(args.data_root)
            if row is None:
                raise AirootError(
                    "NOT_FOUND",
                    f"unknown data root: {args.data_root}",
                    evidence=[item["data_root_id"] for item in registry.data_roots(active_only=False)],
                )
            rows = [row]
        else:
            rows = registry.data_roots()

        reports: list[dict[str, Any]] = []
        missing: list[str] = []
        recorded = 0
        for row in rows:
            data_root = data_root_from_row(row)
            try:
                report = discover_data_root(
                    path=Path(data_root.path), data_root_id=data_root.data_root_id, whitelist=rules
                )
            except AirootError as error:
                # The code travels with the entry (draft §103). Keeping only the prose made this the
                # one fold of the three that threw the machine-readable cause away, and it was right
                # only because `discover_data_root` happened to have exactly one raise: the day it
                # grows a second one, the aggregate below would report the wrong code *and* the wrong
                # exit code with a sentence as the only clue.
                missing.append(
                    {
                        "data_root_id": data_root.data_root_id,
                        "reason_code": error.reason_code,
                        "detail": error.message,
                    }
                )
                continue
            document = report.to_document()
            if args.record:
                recorded += _record_observations(registry, report, data_root)
            reports.append(document)

        # Derived state is derived: recording observations appends events, so the JSON
        # projection and the audit digest must be rewritten in the same command. Leaving this
        # to "only when a data root is missing" made `doctor` report AUDIT_PROJECTION_DRIFT
        # right after a successful `discover --record` (found on the real machine).
        if recorded or missing:
            registry.update_projection()
    finally:
        registry.close()

    document = {
        "schema_version": 1,
        "whitelist_revision": rules.revision,
        "recorded": recorded,
        "reports": reports,
        "missing": missing,
        # The **aggregate**: "a declared data root could not be read" is a state problem (exit 6),
        # whatever the specific cause was — the cause travels in `missing[].reason_code` (draft §103).
        # `root status` folds to the document's own code because its document *is* about the registry;
        # here several roots can fail at once, so the top level states the class and the rows state the
        # cause.
        "reason_code": "DATA_ROOT_MISSING" if missing else "SUCCESS",
        "files_touched": 0,
    }
    lines = [f"discover: {len(reports)} data root(s), {sum(len(r['candidates']) for r in reports)} object(s)"]
    for report in reports:
        lines.append(f"  {report['data_root_id']}: {report['counts']}")
    lines.extend(f"  missing: {item['data_root_id']} ({item['reason_code']}): {item['detail']}" for item in missing)
    _emit(document, as_json=args.json, lines=lines)
    return document, exit_code_for(document["reason_code"])


def _record_observations(registry: Registry, report: Any, data_root: Any) -> int:
    """Persist non-owning observations for unmatched/quarantined objects.

    ``external_reference`` rows are deliberately *not* written here: adoption is an
    explicit action (v0.3 §9.6, S-026). ``excluded`` is a discovery classification
    that has no registry representation, so it stays in the report only.
    """

    from .registry import ExternalReference

    recordable = [item for item in report.candidates if item.management in {"unmanaged", "quarantined"}]
    if not recordable:
        return 0
    with registry.write(expected_generation=registry.generation) as connection:
        for candidate in recordable:
            registry.upsert_external_reference(
                connection,
                ExternalReference(
                    external_id=candidate.external_id,
                    capability_id=candidate.capability_id or "unknown",
                    path=str(candidate.object_root),
                    management=candidate.management,
                    health="healthy",
                    observed_at=registry.clock.timestamp(),
                    data_root_id=data_root.data_root_id,
                    evidence=tuple(candidate.notes and [{"kind": "discovery", "detail": note} for note in candidate.notes] or ()),
                ),
            )
        registry.append_event(
            connection,
            state="DISCOVERY_RECORDED",
            detail=f"recorded {len(recordable)} unmanaged/quarantined observation(s) for {data_root.data_root_id}",
            outcome="ok",
        )
    return len(recordable)


def cmd_adopt(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Establish a reference to an object inside a registered data root."""

    from .canon import digest_file
    from .caps.discovery import classify_object, load_whitelist
    from .paths import canonicalize
    from .registry import ExternalReference
    from .registry.entities import data_root_from_row

    if args.mode == "recreate":
        # `recreate` is not a portable-artifact transaction: 规划 §15.5 defines it as rebuilding a
        # runtime/environment from a version and a project declaration, which needs the P5 runtime
        # extension. The evidence used to blame "the P4 portable transaction" for both modes; §64
        # implemented `import` on top of that transaction, so the claim had to be corrected rather
        # than left standing (draft §64.4).
        raise AirootError(
            "UNSUPPORTED_BACKEND",
            "adopt --mode recreate is not implemented",
            evidence=[
                "规划 §15.5: recreate rebuilds a runtime/environment from a version and a project declaration",
                "that needs the P5 runtime extension, not a portable artifact transaction",
                "use --mode import for a script-free portable file, or --mode reference to record without owning",
            ],
        )

    if args.mode == "import":
        return _adopt_import(args, context)

    registry = context.registry()
    try:
        target = canonicalize(args.path, must_exist=True)
        owner = next(
            (
                row
                for row in registry.data_roots()
                if Path(_claim_spelling(str(row["path"]))) == target.parent
            ),
            None,
        )
        if owner is None:
            raise AirootError(
                "INVALID_INPUT",
                "adopt only accepts objects that sit directly inside a registered data root",
                evidence=[
                    f"requested={target}",
                    f"registered roots={[row['path'] for row in registry.data_roots()]}",
                ],
            )
        data_root = data_root_from_row(owner)
        candidate = classify_object(target, data_root_id=data_root.data_root_id, whitelist=load_whitelist())
        if candidate.management != "external_reference":
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"no whitelisted capability matches {target.name}",
                evidence=[*candidate.notes, "freeze the capability and add a whitelist entry first"],
            )

        observed_digest = None
        if candidate.executable is not None and candidate.executable.is_file():
            observed_digest = digest_file(candidate.executable)

        reference = ExternalReference(
            external_id=candidate.external_id,
            capability_id=str(candidate.capability_id),
            path=str(target),
            management="external_reference",
            health="healthy",
            observed_at=context.clock.timestamp(),
            capability_kind=candidate.kind,
            data_root_id=data_root.data_root_id,
            version=candidate.version,
            versions=tuple(candidate.versions),
            active_version=candidate.active_version,
            architecture=candidate.architecture,
            entrypoints=candidate.entrypoints,
            observed_digest=observed_digest,
            probe_level=candidate.probe_level,
            source_kind=candidate.source_kind,
            evidence=tuple(candidate.evidence),
        )
        with registry.write(expected_generation=registry.generation) as connection:
            registry.upsert_external_reference(connection, reference)
            registry.append_event(
                connection,
                state="REFERENCE_ADDED",
                detail=f"reference {reference.external_id} -> {target} (capability={reference.capability_id}, ownership=none)",
                outcome="ok",
            )
        registry.update_projection()
    finally:
        registry.close()

    document = {
        "schema_version": 1,
        "reference": reference.to_schema(),
        "ownership": "none",
        "files_touched": 0,
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"reference {reference.external_id} -> {target}",
            f"  capability={reference.capability_id} version={reference.version} arch={reference.architecture}",
            *(
                [f"  versions={', '.join(reference.versions)} active={reference.active_version or 'unknown'}"]
                if len(reference.versions) > 1
                else []
            ),
            "  ownership: none (deleting AIROOT will not touch this object)",
        ],
    )
    return document, EXIT_SUCCESS


def _adopt_import(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """`adopt --mode import`: plan the controlled copy of a local script-free payload (draft §64).

    What is delivered here is the **plan**, not the install, and that is the contract's shape rather
    than a shortcut: 规划 §15.5 says import copies a verifiable portable object "通过 plan/approval",
    the core may never mint an approval (`AGENTS.md` §7), and `approve` + `install` already drive any
    backend a plan names (`_runner_for`, draft §21.5). So this command is the missing **entry point**
    for a file the user already has — until §64 there was none: `plan build` can only take a resolved
    document from `source resolve`, i.e. an artifact fetched from a trusted upstream.

    Two things are deliberately *not* invented. The capability is taken from `--capability` rather
    than guessed from the file name — the classifier reads a directory against a data root, and a
    loose file has no such context; P1's rule for identities it cannot discover is to accept them as
    explicit input (`machine_id`/`session_id`/`project_id`, §17). And the digest is computed from the
    file, which is honest for an import (it pins *these* bytes so the transaction can notice them
    changing) but is **not** provenance, so the plan says so in as many words.
    """

    from .canon import plan_hash
    from .caps.backends import assert_script_free, resolve_backend, sha256_file
    from .caps.boundary import load_capabilities
    from .paths import canonicalize
    from .schema_io import validate_self
    from .tx.artifact import create_artifact_plan

    if not args.capability:
        raise AirootError(
            "INVALID_INPUT",
            "adopt --mode import needs --capability",
            evidence=[
                "a loose file carries no data-root context, so its capability cannot be discovered",
                "P1 accepts an identity it cannot observe as explicit input rather than guessing it",
                "example: adopt D:\\downloads\\7z.exe --mode import --capability archive --version 24.09",
            ],
        )
    capability = str(args.capability)
    if "/" in capability or "\\" in capability or ".." in capability:
        # The published `id` pattern allows `/` and `.`, because an *instance* id nests
        # (`jq/jq/1.7.1/win-x64`). A capability id is a name: it becomes a path component under
        # `store/` and part of the binding key, so a separator or a traversal is refused here with a
        # stable code instead of surfacing later as a schema/self-validation failure.
        raise AirootError(
            "INVALID_INPUT",
            f"a capability id is a name, not a path: {capability!r}",
            evidence=["it becomes part of store/<instance_id> and of the binding key"],
        )

    # §69: the frozen capability list decides what AIROOT may manage at all (draft §15.2-1: "no frozen
    # capability -> unmanaged: reported, never adopted"). `plan` has always enforced that, and this is
    # the *other* entry point that produces a plan for a managed instance — so without this check the
    # boundary would be advisory for whoever typed `adopt --mode import` instead of `plan`. The message
    # matches `plan`'s word for word on purpose: the two entry points must not read as two rules.
    frozen = load_capabilities()
    if frozen.by_id(capability) is None:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"no frozen capability is declared for {capability}",
            evidence=[
                f"revision {frozen.revision} declares: {', '.join(frozen.ids())}",
                "the boundary is what AIROOT may manage at all; no frozen capability means unmanaged",
                "to manage it: propose the capability, freeze it in policy/capabilities.json, then add "
                "a whitelist evidence predicate (draft §15.4)",
            ],
        )

    target = canonicalize(args.path, must_exist=True)
    if target.is_dir():
        raise AirootError(
            "INVALID_INPUT",
            "adopt --mode import takes one script-free portable file",
            evidence=[
                f"{target} is a directory",
                "a runtime tree is not a v1 portable artifact (三大核心契约 决策3)",
                "use --mode reference to record a directory without taking ownership",
            ],
        )
    assert_script_free(target)

    # §70: `plan` and `adopt --mode import` both produce a plan for a managed instance, and §12.1
    # makes confirmation *mandatory* for the high-risk classes. The import path never consulted that
    # gate, so a frozen runtime (or an over-threshold payload) went straight to a machine-scope
    # binding — the class the routing layer refuses until a human answers "where should it live".
    # Import has no way to ask: it binds machine scope by definition (§15.5), and the answer is an
    # interactive human step this build cannot take yet (the memory channel is read-only until P2).
    # So it refuses, exactly as `plan` does, instead of making the gate decorative for the class the
    # contract singles out as high-risk. The size is *measured* here, not estimated: the file is
    # already on disk.
    from .caps.planner import import_scope_decision

    source_bytes = target.stat().st_size
    routing = import_scope_decision(capability, source_bytes=source_bytes)
    if routing.confirmation_required:
        raise AirootError(
            "SCOPE_CONFIRMATION_REQUIRED",
            f"importing {capability} needs confirmation before a plan may be produced",
            evidence=[
                *[item["detail"] for item in routing.evidence if item["kind"] == "high_risk"],
                "options: " + " / ".join(routing.options),
                f"measured size={source_bytes} bytes (threshold={routing.threshold_bytes})",
                "import binds machine scope and cannot ask which scope you want; answer the question "
                "through `plan`, or record the object without owning it via --mode reference",
            ],
        )

    registry = context.registry()
    try:
        version = str(args.version or "unversioned")
        plan = create_artifact_plan(
            registry,
            resolve_backend("portable_file", root=context.path()),
            capability_id=capability,
            version=version,
            kind="managed_tool",
            locator=str(target),
            source_digest=sha256_file(target),
            clock=context.clock,
        )
        # `create_artifact_plan` fills a publisher from `requested_by`, which for an imported file
        # would name whoever ran the command as if they were the publisher. There is no publisher:
        # the caller handed over bytes. Replaced, and the plan is hashed again after the change.
        #
        # The caveat itself goes in `metadata` rather than beside the digest, because
        # `source.provenance` is a **closed** object in the published schema (`additionalProperties:
        # false`, exactly `source_id`/`publisher`/`retrieved_at`). The first version of this put a
        # `note` in there and the core refused its own document with `SELF_VALIDATION_FAILED` —
        # which is the guard working: a plan this build cannot validate is an implementation defect,
        # never something to wave through (draft §64.4).
        plan["source"]["provenance"] = {"source_id": "local-import", "publisher": None}
        plan["metadata"]["import"] = {
            "origin": "local_file",
            "version": version,
            "version_source": "declared-by-caller" if args.version else "no-version-in-the-file",
            # The size is a fact this command already has (it hashed every byte). It used to appear
            # nowhere in the plan — `metadata.backend.estimated_size` was null and `source.integrity`
            # carries digests only — so the person approving a 250 MB copy could not see its size
            # (draft §70.2).
            "size_bytes": source_bytes,
            "size_source": "measured-from-the-file",
            "routing": {
                "reason_code": routing.reason_code,
                "origin": routing.origin,
                "scope": routing.scope,
                "threshold_bytes": routing.threshold_bytes,
            },
            "note": (
                "supplied by the caller; the sha256 pins these bytes and attests no origin "
                "(v1 verifies digests, never signatures)"
            ),
        }
        plan["plan_hash"] = plan_hash(plan)
        validate_self("plan", plan)
        plan_file = _plan_path(context, plan["plan_id"])
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        registry.close()

    plan["plan_file"] = str(plan_file)
    plan["reason_code"] = "SUCCESS"
    plan["required_action"] = (
        f"approve {plan['plan_hash']}, then `airoot install {plan_file} --token-file <token.json>`"
    )
    _emit(
        plan,
        as_json=args.json,
        lines=[
            f"import plan {plan['plan_id']} -> {plan_file}",
            f"  artifact {target}",
            f"  digest {plan['source']['integrity']['artifact_digest']} (pins these bytes; attests no origin)",
            f"  size {source_bytes} bytes (measured; the confirmation threshold is {routing.threshold_bytes})",
            *(
                ["  no version in the file: recorded as 'unversioned'; pass --version to name it"]
                if not args.version
                else []
            ),
            "  nothing was copied yet: approve and install to take ownership",
        ],
    )
    return plan, EXIT_SUCCESS


def cmd_forget(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Drop AIROOT's record of a reference. The referenced files are never touched."""

    from .canon import digest_file

    registry = context.registry()
    try:
        row = registry.external_reference(args.external_id)
        if row is None:
            raise AirootError(
                "NOT_FOUND",
                f"unknown reference: {args.external_id}",
                evidence=[item["external_id"] for item in registry.external_references()],
            )
        path = Path(row["path"])
        before = digest_file(path) if path.is_file() else None
        with registry.write(expected_generation=registry.generation) as connection:
            registry.forget_external_reference(connection, args.external_id)
            registry.append_event(
                connection,
                state="REFERENCE_REMOVED",
                detail=f"reference {args.external_id} removed; source untouched",
                outcome="ok",
            )
        registry.update_projection()
    finally:
        registry.close()

    after = digest_file(path) if path.is_file() else None
    document = {
        "schema_version": 1,
        "external_id": args.external_id,
        "files_touched": 0,
        "source_unchanged": before == after,
        "project_manifest_check": "not_implemented_before_p6",
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[f"reference {args.external_id} removed; the referenced object was not touched"],
    )
    return document, EXIT_SUCCESS


def cmd_scope_decide(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Answer "where should this go, and do I have to ask?" — and nothing else."""

    from .caps.planner import ScopeRequest, decide_scope, read_tooling_memory
    from .paths import canonicalize

    project_root: Path | None = None
    if args.project:
        project_root = canonicalize(args.project, must_exist=True)
        if not project_root.is_dir():
            raise AirootError("INVALID_INPUT", f"--project must be a directory: {project_root}")

    request = ScopeRequest(
        capability_id=args.capability,
        project_root=project_root,
        intent=args.intent,
        creates_environment=args.creates_environment,
        requires_cuda_or_native=args.requires_cuda_or_native,
        declared_size_bytes=args.size_bytes,
        source_verifiable=not args.source_unverifiable,
    )
    memory = read_tooling_memory(project_root) if project_root else None
    decision = decide_scope(request, memory=memory)
    document = decision.to_document()

    lines = [
        f"{decision.capability_id}: scope={decision.scope} "
        f"confirmation_required={decision.confirmation_required} ({decision.origin})"
    ]
    if decision.confirmation_required:
        lines.append(f"  options: {' / '.join(decision.options)}")
    if decision.size_source == "unknown":
        lines.append("  size: unknown (not estimated rather than guessed)")
    lines.extend(f"  - {item['detail']}" for item in decision.evidence)
    _emit(document, as_json=args.json, lines=lines)
    return document, exit_code_for(decision.reason_code)


def cmd_scope_memory(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Show the recorded routing choices for a project (read-only)."""

    from .caps.planner import (
        TOOLING_MEMORY_RELATIVE,
        manifest_fingerprint,
        read_tooling_memory,
        tooling_memory_path,
    )
    from .paths import canonicalize

    project_root = canonicalize(args.project, must_exist=True)
    if not project_root.is_dir():
        raise AirootError("INVALID_INPUT", f"--project must be a directory: {project_root}")
    memory = read_tooling_memory(project_root)
    document = {
        "schema_version": 1,
        "project_root": str(project_root),
        "memory_path": str(tooling_memory_path(project_root)),
        "present": memory is not None,
        "manifest_fingerprint": manifest_fingerprint(project_root),
        "memory": memory,
        "writable": False,
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{TOOLING_MEMORY_RELATIVE}: {'present' if memory else 'absent'}",
            "  writing it belongs to the human approval channel (P2); this command is read-only",
        ],
    )
    return document, EXIT_SUCCESS


def _reference_row(registry: Any, external_id: str) -> Any:
    row = registry.external_reference(external_id)
    if row is None:
        raise AirootError(
            "NOT_FOUND",
            f"unknown reference: {external_id}",
            evidence=[item["external_id"] for item in registry.external_references()],
        )
    if row["management"] != "external_reference":
        raise AirootError(
            "OWNERSHIP_REQUIRED",
            f"{external_id} is not an external reference (management={row['management']})",
            evidence=["AIROOT never persists an object it owns; use session activation instead"],
        )
    return row


def _exposure_target(row: Any) -> Any:
    from .caps.exposure import ExposureTarget
    from .registry.entities import external_reference_from_row

    reference = external_reference_from_row(row)
    path = Path(reference.path)
    entrypoints = list(reference.entrypoints)
    entrypoint_dir = (path / Path(entrypoints[0])).parent if entrypoints else path
    return ExposureTarget(
        external_id=reference.external_id,
        capability_id=reference.capability_id,
        path=path,
        object_root=path,
        entrypoint_dir=entrypoint_dir,
    )


def _whitelist_environment(capability_id: str) -> dict[str, Any] | None:
    from .caps.discovery import load_whitelist

    rule = next(
        (item for item in load_whitelist().entries if item.capability_id == capability_id), None
    )
    return dict(rule.environment) if rule is not None and rule.environment else None


def cmd_env_activate(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Session activation: print shell code, and (with --session) record a snapshot (规划 §16.4)."""

    from .caps.environment import WindowsEnvironmentStore, resolve_path_entries, resolve_variables, spec_from_entry
    from .caps.session import pop_frames, push_frame, read_stack

    registry = context.registry()
    try:
        row = _reference_row(registry, args.external_id)
        target = _exposure_target(row)
        spec = spec_from_entry(target.capability_id, _whitelist_environment(target.capability_id))
        variables = resolve_variables(
            spec, object_root=target.object_root, entrypoint_dir=target.entrypoint_dir
        )
        entries = [
            str(item)
            for item in resolve_path_entries(
                spec, object_root=target.object_root, entrypoint_dir=target.entrypoint_dir
            )
        ]
        generation = registry.generation
        stack: list[Any] = read_stack(context.path(), args.session) if args.session else []
        snapshot_id: str | None = None
        diff: dict[str, Any] = {}
        if args.session:
            # The snapshot records what the environment looked like *before* this activation, so a
            # later `deactivate` can restore rather than delete (draft §25.3-3).
            store = WindowsEnvironmentStore() if args.session else None
            frame, stack = push_frame(
                context.path(),
                session_id=args.session,
                external_id=target.external_id,
                capability_id=target.capability_id,
                variables=variables,
                path_variable="Path",
                path_entries=entries,
                read_current=store.read,
                generation=generation,
                clock=context.clock,
            )
            snapshot_id = frame.snapshot_id
            diff = {name: change.to_document() for name, change in frame.variables.items()}
            if set(frame.variables) != set(variables):
                diff = {name: change.to_document() for name, change in frame.variables.items()}
    finally:
        registry.close()

    if not variables and not entries:
        raise AirootError(
            "CAPABILITY_NOT_DECLARED",
            f"capability {target.capability_id} declares no environment",
            evidence=["add an 'environment' block to its whitelist entry"],
        )

    document = {
        "schema_version": 1,
        "external_id": target.external_id,
        "capability_id": target.capability_id,
        "scope": "session",
        "shell": args.shell,
        "variables": variables,
        "path_prepend": entries,
        "persisted": False,
        "session_id": args.session,
        "source_generation": generation,
        "snapshot_id": snapshot_id,
        "diff": diff,
        "stack_depth": len(stack),
        "deactivate": (
            {"command": f"airoot env deactivate --session {args.session}", "snapshot_id": snapshot_id}
            if args.session
            else {"note": "no --session was given, so there is nothing to deactivate"}
        ),
        "reason_code": "SUCCESS",
    }
    script = _activation_script(args.shell, variables, entries)
    if args.json:
        document["script"] = script
        _emit(document, as_json=True)
        return document, EXIT_SUCCESS
    print(script)
    return document, EXIT_SUCCESS


def cmd_env_deactivate(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Pop the session activation stack and print the script that restores the previous state."""

    from .caps.session import pop_frames, require_fresh, restore_script

    registry = context.registry()
    try:
        generation = registry.generation
        if args.session:
            require_fresh(context.path(), args.session, current_generation=generation)
        popped, remaining = pop_frames(
            context.path(), session_id=args.session, all_frames=args.all
        )
    finally:
        registry.close()

    if not popped:
        document = {
            "schema_version": 1,
            "session_id": args.session,
            "deactivated": [],
            "stack_depth": 0,
            "restored": {"variables": [], "path_entries": []},
            "persisted": False,
            "reason_code": "SUCCESS",
            "note": "the session stack was already empty; nothing to undo",
        }
        _emit(document, as_json=args.json, lines=["session stack is empty; nothing to undo"])
        return document, EXIT_SUCCESS

    restored_variables: list[str] = []
    added_paths: list[str] = []
    for frame in popped:
        restored_variables.extend(sorted(frame.variables))
        added_paths.extend(frame.path_entries)
    script = restore_script(popped, shell=args.shell)
    document = {
        "schema_version": 1,
        "session_id": args.session,
        "deactivated": [frame.external_id for frame in popped],
        "snapshot_ids": [frame.snapshot_id for frame in popped],
        "restored": {"variables": sorted(set(restored_variables)), "path_entries": sorted(set(added_paths))},
        "stack_depth": len(remaining),
        "persisted": False,
        "script": script,
        "reason_code": "SUCCESS",
    }
    if args.json:
        _emit(document, as_json=True)
        return document, EXIT_SUCCESS
    print(script)
    return document, EXIT_SUCCESS



def _activation_script(shell: str, variables: dict[str, str], entries: list[str]) -> str:
    lines: list[str] = []
    if shell == "powershell":
        for name, value in sorted(variables.items()):
            lines.append(f"$env:{name} = '{value}'")
        if entries:
            joined = ";".join(entries)
            lines.append(f"$env:Path = '{joined};' + $env:Path")
    else:
        for name, value in sorted(variables.items()):
            lines.append(f'set "{name}={value}"')
        if entries:
            lines.append(f'set "Path={";".join(entries)};%Path%"')
    return "\n".join(lines) + "\n"


def cmd_env_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    from .registry.entities import environment_persist_from_row

    registry = context.registry()
    try:
        rows = registry.environment_persist_records(active_only=not args.all)
        records = [environment_persist_from_row(row).to_document() for row in rows]
    finally:
        registry.close()

    document = {"schema_version": 1, "environment_persist": records, "reason_code": "SUCCESS"}
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{item['capability_id']}  {item['scope']}  {item['variable']} = {item['value']}"
            for item in records
        ]
        or ["AIROOT has not persisted any environment variable"],
    )
    return document, EXIT_SUCCESS


def cmd_env_persist(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Persist a reference's environment. Needs an approval token bound to the plan."""

    from .schema_io import validate_self
    from .caps.exposure import (
        ExposureRequest,
        SCOPE_USER,
        apply_reference_plan,
        build_reference_plan,
        request_from_entry,
    )

    registry = context.registry()
    plan: dict[str, Any]
    try:
        if args.plan_file:
            plan = _load_document(Path(args.plan_file), "reference-plan", "INVALID_PLAN")
            external_id = str(plan["target"]["external_id"])
        else:
            row = _reference_row(registry, args.external_id)
            target = _exposure_target(row)
            data_roots = tuple(Path(item["path"]) for item in registry.data_roots())
            request: ExposureRequest = request_from_entry(
                target=target,
                entry=_whitelist_environment(target.capability_id),
                scope=args.scope,
                data_roots=data_roots,
                requested_by=args.requested_by,
            )
            plan = build_reference_plan(
                request, registry=registry, clock=context.clock, plan_id=args.plan_id
            )
            external_id = target.external_id

        # §90: this document is printed below, so it gets the pre-print self-check every other
        # outward document gets (AGENTS.md §7: the core calls `validate_self` before printing any
        # outward JSON). The `--plan-file` path already validates on load; the **built** path did not,
        # which made `reference-plan` the one printed document this build never checked.
        validate_self("reference-plan", plan)

        plan_file = _plan_path(context, str(plan["plan_id"]))
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        if args.dry_run:
            document = dict(plan)
            document["plan_file"] = str(plan_file)
            document["reason_code"] = "SUCCESS"
            document["dry_run"] = True
            _emit(
                document,
                as_json=args.json,
                lines=[
                    f"plan {plan['plan_id']} -> {plan_file}",
                    f"  hash {plan['plan_hash']}",
                    "  dry run: nothing was written",
                ],
            )
            return document, EXIT_SUCCESS

        if not args.token_file:
            document = dict(plan)
            document["plan_file"] = str(plan_file)
            document["reason_code"] = "PERSISTENCE_REQUIRES_APPROVAL"
            document["required_action"] = (
                f"approve {plan['plan_hash']} and re-run with --token-file <token.json>"
            )
            _emit(
                document,
                as_json=args.json,
                lines=[
                    f"plan {plan['plan_id']} -> {plan_file}",
                    f"  hash {plan['plan_hash']}",
                    "  persistent environment changes need an approval token (--token-file)",
                    "  nothing was written; use --dry-run to only inspect the plan",
                ],
            )
            return document, exit_code_for("PERSISTENCE_REQUIRES_APPROVAL")

        token = _load_token(args.token_file)
        verify_approval(registry, plan, token, keyring=load_keyring(context.path()), clock=context.clock)
        record_approval(registry, token)
        result = apply_reference_plan(
            plan,
            registry=registry,
            approval_id=str(token["approval_id"]),
            approval_mode=str(token["approval_mode"]),
            clock=context.clock,
        )
    finally:
        registry.close()

    document = result.to_document()
    document["external_id"] = external_id
    document["plan_file"] = str(plan_file)
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"persisted for {external_id} (scope={document['scope']}): {', '.join(sorted(document['variables']))}",
            "  every previous value was recorded; 'airoot env forget' reverses this exactly",
        ],
    )
    return document, EXIT_SUCCESS


def cmd_env_forget(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Restore the values AIROOT replaced. Never touches the referenced object.

    Two questions, one verb, and they are not interchangeable: "stop managing this reference" names
    a capability, while `--all` is the steward leaving — put back everything AIROOT ever wrote
    (draft §13.4). Combining them would be ambiguous, so it is refused rather than resolved by
    precedence.
    """

    from .caps.exposure import forget_all_persist, forget_reference_persist

    if args.all and (args.external_id or args.variable):
        raise AirootError(
            "INVALID_INPUT",
            "--all restores everything AIROOT persisted; it cannot be combined with an id or --variable",
            evidence=["usage: airoot env forget --all [--dry-run]", "or: airoot env forget <external-id>"],
        )
    if not args.all and not args.external_id:
        raise AirootError(
            "INVALID_INPUT",
            "env forget needs a reference id, or --all",
            evidence=["usage: airoot env forget <external-id>", "or: airoot env forget --all"],
        )

    registry = context.registry()
    try:
        if args.all:
            all_result = forget_all_persist(
                registry=registry, clock=context.clock, dry_run=args.dry_run
            )
            document = all_result.to_document()
            lines = [
                f"restored {', '.join(document['restored']) or '-'} "
                f"(removed {', '.join(document['removed']) or '-'})",
                f"  records: {document['records']} across {len(document['capability_ids'])} capability(ies)",
                *(
                    [f"  written by more than one capability: {', '.join(document['shared_variables'])}"]
                    if document["shared_variables"]
                    else []
                ),
                *(
                    [f"  drifted (changed outside AIROOT, restored anyway): {', '.join(document['drifted'])}"]
                    if document["drifted"]
                    else []
                ),
                *(["  dry run: nothing was written"] if args.dry_run else []),
                *(
                    ["  nothing AIROOT wrote is recorded: there is nothing to restore"]
                    if not document["records"]
                    else []
                ),
            ]
            _emit(document, as_json=args.json, lines=lines)
            return document, EXIT_SUCCESS

        row = _reference_row(registry, args.external_id)
        result = forget_reference_persist(
            registry=registry,
            capability_id=str(row["capability_id"]),
            variable=args.variable,
            clock=context.clock,
            dry_run=args.dry_run,
        )
    finally:
        registry.close()

    document = result.to_document()
    document["external_id"] = args.external_id
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"restored {', '.join(sorted(document['restored'])) or '-'} "
            f"(removed {', '.join(sorted(document['removed'])) or '-'})",
            *(
                [f"  drifted (changed outside AIROOT, left alone): {', '.join(sorted(document['drifted']))}"]
                if document["drifted"]
                else []
            ),
            *(["  dry run: nothing was written"] if args.dry_run else []),
        ],
    )
    return document, EXIT_SUCCESS


def cmd_exec(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Run one child process with a reference's environment injected into it."""

    import subprocess

    from .caps.environment import resolve_path_entries, resolve_variables, spec_from_entry

    if not args.child_command:
        # Without a child there is nothing to run; before this guard the CLI reached
        # `subprocess.run([])` and died with a traceback instead of an invalid-input answer.
        raise AirootError(
            "INVALID_INPUT",
            "exec needs a child command",
            evidence=["usage: airoot exec <external-id> -- <command> ..."],
        )

    registry = context.registry()
    try:
        row = _reference_row(registry, args.external_id)
        target = _exposure_target(row)
        spec = spec_from_entry(target.capability_id, _whitelist_environment(target.capability_id))
        variables = resolve_variables(
            spec, object_root=target.object_root, entrypoint_dir=target.entrypoint_dir
        )
        entries = [
            str(item)
            for item in resolve_path_entries(
                spec, object_root=target.object_root, entrypoint_dir=target.entrypoint_dir
            )
        ]
    finally:
        registry.close()

    import os

    child_environment = dict(os.environ)
    child_environment.update(variables)
    if entries:
        key = next((name for name in ("Path", "PATH") if name in child_environment), "Path")
        child_environment[key] = ";".join(entries) + ";" + child_environment.get(key, "")

    # `--json` is machine mode: the child's output must not be interleaved with the
    # document, or the caller cannot parse its own tool's reply. Without `--json` the
    # child inherits this process's streams, which is what an interactive caller wants.
    capture = bool(args.json)
    completed = subprocess.run(
        args.child_command,
        env=child_environment,
        check=False,
        capture_output=capture,
        text=capture,
        encoding="utf-8" if capture else None,
        errors="replace" if capture else None,
    )
    document: dict[str, Any] = {
        "schema_version": 1,
        "external_id": target.external_id,
        "scope": "session",
        "command": list(args.child_command),
        "exit_status": completed.returncode,
        "variables": sorted(variables),
        "path_prepend": entries,
        "persisted": False,
        "reason_code": "SUCCESS" if completed.returncode == 0 else "CHILD_PROCESS_FAILED",
    }
    if capture:
        document["stdout"] = _bounded(completed.stdout or "")
        document["stderr"] = _bounded(completed.stderr or "")
    _emit(document, as_json=args.json)
    # The frozen exit-code space is 0-9 (v0.3 §5.2): a child's status is reported in
    # `exit_status`, never leaked into AIROOT's own exit code.
    return document, EXIT_SUCCESS if completed.returncode == 0 else EXIT_DEGRADED


CHILD_OUTPUT_LIMIT = 65536


def _bounded(text: str) -> str:
    """Keep the tail: a long child output must not make the CLI unusable."""

    if len(text) <= CHILD_OUTPUT_LIMIT:
        return text
    return "...[truncated]...\n" + text[-CHILD_OUTPUT_LIMIT:]


def cmd_tool_retire(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Retire an owned instance: the binding goes, the payload stays (draft §14.1)."""

    from .caps.lifecycle import retire

    registry = context.registry()
    try:
        document = retire(registry, args.target, clock=context.clock)
    finally:
        registry.close()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{document['instance_id']} retired (payload retained: {not document['payload_removed']})",
            "  active binding cleared; use 'tool gc --plan' to see what may be collected",
        ],
    )
    return document, EXIT_SUCCESS


def cmd_tool_gc(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """``gc --plan`` lists collectable payloads; ``gc --apply`` deletes one, with approval."""

    from .caps.lifecycle import apply_gc_plan, build_gc_plan, gc_candidates

    registry = context.registry()
    plan_file: Path | None = None
    try:
        if args.apply:
            if not args.token_file:
                raise AirootError(
                    "APPROVAL_REQUIRED",
                    "gc --apply needs an approval token bound to the plan hash",
                    evidence=["nothing was deleted", "run 'tool gc --plan' first, then approve the plan"],
                )
            token = _load_token(args.token_file)
            plan = _locate_gc_plan(context, args, token)
            document = apply_gc_plan(
                registry, plan, token, root=context.path(), clock=context.clock
            )
        else:
            candidates = [
                item.to_document() for item in gc_candidates(registry, target=args.instance)
            ]
            collectable = [item for item in candidates if item["collectable"]]
            plans = []
            for item in collectable:
                plan = build_gc_plan(
                    registry,
                    str(item["instance_id"]),
                    clock=context.clock,
                    requested_by=args.requested_by,
                )
                target = _plan_path(context, str(plan["plan_id"]))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                plan_file = target
                plans.append(
                    {
                        "instance_id": plan["target"]["instance_id"],
                        "plan_id": plan["plan_id"],
                        "plan_hash": plan["plan_hash"],
                        "plan_file": str(target),
                        "store_path": plan["metadata"]["store_path"],
                    }
                )
            document = {
                "schema_version": 1,
                "operation": "gc_plan",
                "candidates": candidates,
                "collectable": len(collectable),
                "plans": plans,
                "payloads_removed": 0,
                "reason_code": "SUCCESS",
            }
    finally:
        registry.close()

    if args.apply:
        _emit(
            document,
            as_json=args.json,
            lines=[
                f"collected {document['instance_id']} "
                f"({'payload removed' if document['payload_removed'] else 'payload was already absent'})",
                "  registry row retained for binding history; data roots untouched",
            ],
        )
        return document, EXIT_SUCCESS

    _emit(
        document,
        as_json=args.json,
        lines=[
            f"gc plan: {document['collectable']} collectable of {len(document['candidates'])} instance(s)",
            *[
                f"  {item['instance_id']}: {'collectable' if item['collectable'] else '; '.join(item['blockers'])}"
                for item in document["candidates"]
            ],
            *(
                [f"  approve {document['plans'][0]['plan_hash']} then re-run with --apply --token-file <token>"]
                if document["plans"]
                else []
            ),
        ],
    )
    return document, EXIT_SUCCESS


def _locate_gc_plan(context: Context, args: argparse.Namespace, token: dict[str, Any]) -> dict[str, Any]:
    """Find the plan the token authorises. The hash is the identity, not the filename."""

    if args.plan_file:
        return _load_document(Path(args.plan_file), "plan", "INVALID_PLAN")
    directory = context.path() / "state" / "plans"
    for path in sorted(directory.glob("plan_gc_*.json")) if directory.is_dir() else []:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if candidate.get("plan_hash") == token.get("plan_hash"):
            validate_document("plan", candidate, reason_code="INVALID_PLAN")
            return candidate
    raise AirootError(
        "INVALID_PLAN",
        "no gc plan on disk matches the token's plan hash",
        evidence=[f"plan_hash={token.get('plan_hash')}", f"looked in {directory}"],
    )


def cmd_uninstall(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """``uninstall`` = retire + gc. It never deletes without an approval token."""

    from .caps.lifecycle import apply_gc_plan, uninstall_target

    registry = context.registry()
    plan_file: Path | None = None
    try:
        outcome = uninstall_target(registry, args.target, clock=context.clock)
        plan = outcome["plan"]
        plan_file = _plan_path(context, str(plan["plan_id"]))
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        if args.dry_run or not args.token_file:
            document = {
                "schema_version": 1,
                "operation": "uninstall",
                "retire": outcome["retire"],
                "plan": plan,
                "plan_file": str(plan_file),
                "payload_removed": False,
                "dry_run": bool(args.dry_run),
                "reason_code": "SUCCESS" if args.dry_run else "APPROVAL_REQUIRED",
                "required_action": f"approve {plan['plan_hash']} and re-run with --token-file <token.json>",
            }
            _emit(
                document,
                as_json=args.json,
                lines=[
                    f"{plan['target']['instance_id']} retired; payload retained",
                    f"  plan {plan['plan_id']} -> {plan_file}",
                    f"  hash {plan['plan_hash']}",
                    "  deletion needs an approval token (--token-file); there is no --force",
                ],
            )
            return document, EXIT_SUCCESS if args.dry_run else exit_code_for("APPROVAL_REQUIRED")

        token = _load_token(args.token_file)
        document = apply_gc_plan(registry, plan, token, root=context.path(), clock=context.clock)
        document["retire"] = outcome["retire"]
        document["operation"] = "uninstall"
    finally:
        registry.close()

    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{document['instance_id']} uninstalled: payload removed, registry row retained",
            "  data roots were not touched",
        ],
    )
    return document, EXIT_SUCCESS


def cmd_capability_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """The frozen capability list: what AIROOT is allowed to manage at all (draft §15)."""

    from .caps.boundary import load_capabilities

    frozen = load_capabilities()
    document = {
        "schema_version": 1,
        "revision": frozen.revision,
        "capabilities": [item.to_document() for item in frozen.capabilities],
        "count": len(frozen.capabilities),
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{item['capability_id']}  kind={item['kind']}  entry={item['entry']}  "
            f"scope={'/'.join(item['scope']) or '-'}"
            for item in document["capabilities"]
        ],
    )
    return document, EXIT_SUCCESS


def cmd_capability_check(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Answer "may AIROOT manage this object?" without touching anything (draft §15.2)."""

    from .caps.boundary import check_admission
    from .caps.discovery import classify_object, load_whitelist
    from .paths import canonicalize

    target = canonicalize(args.path, must_exist=True)
    capability_id = args.capability
    evidence_source = "declared"
    if capability_id is None and target.is_dir():
        whitelist = load_whitelist()
        candidate = classify_object(target, whitelist=whitelist)
        capability_id = candidate.capability_id
        evidence_source = f"observed ({candidate.management})"

    admission = check_admission(
        target=target,
        capability_id=capability_id,
        source_verifiable=not args.source_unverifiable,
        requires_irreversible_effect=args.irreversible,
    )
    document = admission.to_document()
    document["capability_source"] = evidence_source
    document["managed_files_touched"] = 0
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{target}: {admission.verdict} (capability={capability_id or 'none'}, source={evidence_source})",
            *[f"  - {item}" for item in admission.evidence],
            *([f"  -> {admission.remediation}"] if admission.remediation else []),
        ],
    )
    return document, exit_code_for(document["reason_code"])


def cmd_source_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """The trusted source catalog: which hosts may be contacted and why (draft §23)."""

    from .caps.sources import load_sources

    catalog = load_sources()
    document = {
        "schema_version": 1,
        "revision": catalog.revision,
        "allowed_hosts": list(catalog.allowed_hosts),
        "sources": [item.to_document() for item in catalog.sources],
        "signature_verification": "not_implemented_in_v1",
        "note": "a digest is not a signature; v1 verifies digests against published checksum files only",
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"catalog {catalog.revision}; allowed hosts: {', '.join(catalog.allowed_hosts)}",
            *[
                f"  {item.capability_id}: {item.artifact_url} (checksum: {item.checksum.format})"
                for item in catalog.sources
            ],
        ],
    )
    return document, EXIT_SUCCESS


def cmd_source_resolve(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Resolve (capability, version) to an artifact plus its **published** digest."""

    from .caps.sources import resolve_source

    resolved = resolve_source(
        capability_id=args.capability,
        version=args.version,
        offline_checksum_path=args.offline_checksum,
    )
    document = resolved.to_document()
    if args.source_out:
        path = Path(args.source_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        document["source_file"] = str(path)
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{resolved.capability_id} {resolved.version} -> {resolved.artifact_url}",
            f"  expected digest: {resolved.expected_digest}",
            f"  checksum from: {resolved.checksum_url}",
            f"  backend: {resolved.backend_id}",
            *(["  signature: not implemented in v1 (a digest is not a signature)"] if True else []),
        ],
    )
    return document, EXIT_SUCCESS


def cmd_rebuild(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Regenerate derived state from authoritative rows (draft §24)."""

    from .caps.rebuild import apply_rebuild, rebuild_plan

    registry = context.registry()
    try:
        if args.plan:
            findings = rebuild_plan(registry, context.path())
            document = {
                "schema_version": 1,
                "operation": "rebuild_plan",
                "generation": registry.generation,
                "findings": findings.to_document(),
                "would_rewrite": ["state/registry.json", "logs/audit/events.json"],
                "reason_code": "SUCCESS",
            }
            _emit(
                document,
                as_json=args.json,
                lines=[
                    f"rebuild plan: derived state {'stale' if findings.derived_stale else 'already current'}",
                    f"  orphans={len(findings.orphans)} unmanaged={len(findings.unmanaged)} "
                    f"unfinished_transactions={len(findings.pending_transactions)}",
                    "  nothing was written",
                ],
            )
            return document, EXIT_SUCCESS
        document = apply_rebuild(registry, context.path(), clock=context.clock)
    finally:
        registry.close()

    findings = document["findings"]
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"rebuilt {', '.join(document['rebuilt'])} at generation {document['generation']}",
            *([f"  previous projections archived to {document['archived_to']}"] if document["archived_to"] else []),
            f"  reported only: {len(findings['orphans'])} orphan(s), {len(findings['unmanaged'])} unmanaged, "
            f"{len(findings['pending_transactions'])} unfinished transaction(s)",
            "  the registry database was not touched; nothing was adopted or deleted",
        ],
    )
    return document, EXIT_SUCCESS


def cmd_tool_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Every AIROOT-owned instance with its facts (规划 §15.4:1601). Read-only."""

    from .caps.toolstate import list_tools

    registry = context.registry()
    try:
        document = list_tools(registry, capability_id=args.capability, root=context.path())
    finally:
        registry.close()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{item['capability_id']} {item['version']} ({item['architecture']})  "
            f"{item['lifecycle_status']}/{item['health']}  "
            f"binding={(item['active_binding'] or {}).get('binding_key', '-')}"
            for item in document["instances"]
        ]
        or ["no AIROOT-owned instances"],
    )
    return document, EXIT_SUCCESS


def cmd_tool_status(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Read and verify one instance's manifest/payload/binding/exposure. Never switches active."""

    from .caps.toolstate import tool_status

    registry = context.registry()
    try:
        status = tool_status(registry, args.target, root=context.path())
    finally:
        registry.close()
    document = status.to_document()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{document['instance']['instance_id']}: {document['instance']['lifecycle_status']}/"
            f"{document['instance']['health']} payload={'present' if document['payload_present'] else 'missing'}",
            *[f"  [{item['severity']}] {item['code']}: {item['detail']}" for item in document["findings"]],
            "  read-only: no active binding was changed",
        ],
    )
    return document, exit_code_for(document["reason_code"])


def cmd_tool_verify(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Digest/payload/entrypoint verification. Never adopts, never repairs (§15.4:1603)."""

    from .caps.toolstate import tool_verify

    registry = context.registry()
    try:
        document = tool_verify(registry, args.target, root=context.path())
    finally:
        registry.close()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{document['instance_id']}: {'verified' if document['verified'] else 'verification failed'} "
            f"({document['reason_code']})",
            *[f"  {item['code']}: {item['detail']}" for item in document["problems"]],
            "  nothing was repaired or adopted",
        ],
    )
    return document, exit_code_for(document["reason_code"])


def cmd_path_verify(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Check the frozen PATH-exposure invariant (规划 §8.1 rule 8). Never writes PATH."""

    from .caps.effective import machine_path, user_path
    from .caps.pathexposure import verify_path_exposure

    verification = verify_path_exposure(
        context.path(), machine_entries=machine_path(), user_entries=user_path()
    )
    document = verification.to_document()
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"sanctioned entry: {document['sanctioned_entry']} "
            f"({'present' if document['launcher_present'] else 'absent'})",
            f"  AIROOT-owned PATH entries: {len(document['entries'])}",
            *[f"  [{item['severity']}] {item['code']}: {item['detail']}" for item in document["findings"]],
            "  PATH was not modified (writing machine PATH belongs to the P2 broker)",
        ],
    )
    return document, exit_code_for(document["reason_code"])


def cmd_tool_pin(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Record an intent and offer a plan that would satisfy it (draft §27, §15.4:1604).

    A pin never changes the active binding: it writes *desired* state and, when the declared state
    does not satisfy it, emits a plan. Applying that plan still needs the normal approval.
    """

    from .caps.desired import clear_pin, evaluate, load_desired, pin

    registry = context.registry()
    plan: dict[str, Any] | None = None
    plan_file: str | None = None
    try:
        if args.clear:
            manifest = clear_pin(context.path(), capability_id=args.capability)
        else:
            if not args.version:
                raise AirootError(
                    "INVALID_INPUT",
                    "pinning needs --version <constraint> (or --clear)",
                    evidence=["a constraint looks like '>=3.11,<3.13' or '1.0.0'"],
                )
            manifest = pin(
                context.path(),
                capability_id=args.capability,
                version=args.version,
                scope=args.scope,
                clock=context.clock,
            )
        sync = [item.to_document() for item in evaluate(registry, manifest)]

        unsatisfied = [item for item in sync if not item["in_sync"]]
        if unsatisfied:
            entry = unsatisfied[0]
            # A plan can only be produced when a trusted source exists; saying "no plan" is more
            # honest than inventing a locator (draft §23: provenance must come from the catalog).
            try:
                from .caps.sources import resolve_source
                from .caps.backends import resolve_backend
                from .tx.artifact import create_artifact_plan

                resolved = resolve_source(
                    capability_id=entry["capability_id"],
                    version=str(args.version or "").lstrip(">=<") or "0.0.0",
                    offline_checksum_path=args.offline_checksum,
                )
                backend = resolve_backend(resolved.backend_id, root=context.path())
                plan = create_artifact_plan(
                    registry,
                    backend,
                    capability_id=entry["capability_id"],
                    version=str(args.version or "0.0.0"),
                    kind="managed_tool",
                    locator=resolved.artifact_url,
                    source_digest=resolved.expected_digest,
                    clock=context.clock,
                    requested_by=args.requested_by,
                )
                plan["metadata"]["pinned_by"] = f"desired/{manifest.manifest_revision}"
                plan["metadata"]["desired_manifest_revision"] = manifest.manifest_revision
                from .canon import plan_hash as _plan_hash
                from .schema_io import validate_self as _validate_self

                plan["plan_hash"] = _plan_hash(plan)
                _validate_self("plan", plan)
                target = _plan_path(context, str(plan["plan_id"]))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                plan_file = str(target)
            except AirootError as error:
                plan = None
                plan_file = None
                unsatisfied[0]["plan_blocked_by"] = error.reason_code
                unsatisfied[0]["plan_blocked_detail"] = error.message
    finally:
        registry.close()

    document = {
        "schema_version": 1,
        "operation": "pin",
        "capability_id": args.capability,
        "manifest_revision": manifest.manifest_revision,
        "manifest_path": str(desired_path_for(context)),
        "desired": [item.to_document() for item in manifest.capabilities],
        "sync": sync,
        "in_sync": all(item["in_sync"] for item in sync),
        "plan": plan,
        "plan_file": plan_file,
        "active_binding_changed": False,
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{args.capability}: desired={args.version or '(cleared)'} "
            f"revision={manifest.manifest_revision}",
            *[f"  {item['capability_id']}: {'in sync' if item['in_sync'] else item['detail']}" for item in sync],
            *([f"  plan {plan['plan_id']} -> {plan_file}"] if plan and plan_file else []),
            *(
                ["  no plan could be built (no trusted source); the pin is recorded"]
                if not plan and any(not item["in_sync"] for item in sync)
                else []
            ),
            "  the active binding was not changed; applying needs the normal approval",
        ],
    )
    return document, EXIT_SUCCESS


def desired_path_for(context: Context) -> Path:
    from .caps.desired import desired_path

    return desired_path(context.path())


def cmd_extension_list(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    manifests = load_manifests()
    registry = context.registry()
    try:
        registered = {str(row["extension_id"]) for row in registry.extensions()}
    finally:
        registry.close()
    document = {
        "schema_version": 1,
        "extensions": [
            {
                "extension_id": manifest["extension_id"],
                "extension_version": manifest["extension_version"],
                "implementation_id": manifest["implementation_id"],
                "capability_types": list(manifest["capability_types"]),
                "operations": sorted(manifest["operations"]),
                "registered": manifest["extension_id"] in registered,
            }
            for manifest in manifests.values()
        ],
        "reason_code": "SUCCESS",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[f"{item['extension_id']} {item['extension_version']} ({', '.join(item['capability_types'])})" for item in document["extensions"]]
        or ["no extensions found"],
    )
    return document, EXIT_SUCCESS


def cmd_extension_status(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    manifests = load_manifests()
    manifest = manifests.get(args.extension_id)
    if manifest is None:
        raise AirootError(
            "EXTENSION_UNAVAILABLE",
            f"unknown extension: {args.extension_id}",
            evidence=sorted(manifests),
        )
    extension = FakeExtension(manifest, clock=context.clock)
    document = extension.run(args.operation or "status")
    _emit(document, as_json=args.json, lines=[f"{document['extension_id']} {document['operation']}: {document['status']}"])
    return document, EXIT_SUCCESS


def _resolve_plan_target(registry: Any, args: argparse.Namespace) -> tuple[str, str | None, str]:
    """Turn ``--scope``/``--target`` into a concrete destination (draft §12.4).

    Returns ``(scope, target_id, target_path)``. A data-root target must be registered, and a
    path target must exist: "typo in the path" and "the target is not there" are different
    problems and must not collapse into one.
    """

    from .paths import canonicalize

    scope = args.scope or "machine"
    if scope == "project":
        project = args.project or args.target
        if not project:
            raise AirootError("INVALID_INPUT", "--scope project needs --project <dir> or --target <dir>")
        root = canonicalize(project, must_exist=True)
        if not root.is_dir():
            raise AirootError("INVALID_INPUT", f"the project target must be a directory: {root}")
        return scope, None, str(root)
    if scope == "data-root":
        target = args.target or ""
        if not target:
            raise AirootError("INVALID_INPUT", "--scope data-root needs --target data-root:<id>")
        if not target.startswith("data-root:"):
            raise AirootError(
                "INVALID_INPUT",
                f"a data-root target must be written data-root:<id>, got {target!r}",
                evidence=[f"known data roots: {[row['data_root_id'] for row in registry.data_roots()]}"],
            )
        data_root_id = target.split(":", 1)[1]
        row = registry.data_root(data_root_id)
        if row is None:
            raise AirootError(
                "DATA_ROOT_MISSING",
                f"unknown data root: {data_root_id}",
                evidence=[str(item["data_root_id"]) for item in registry.data_roots(active_only=False)],
            )
        return scope, data_root_id, str(row["path"])
    return scope, None, args.target or ""


def cmd_plan(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Route, then plan: one approved artifact that says *where* as well as *what* (draft §12.4)."""

    from .canon import plan_hash
    from .caps.planner import (
        DEFAULT_SIZE_THRESHOLD_BYTES,
        SCOPE_DATA_ROOT,
        SCOPE_PROJECT,
        ScopeRequest,
        decide_scope,
    )
    from .schema_io import validate_self

    registry = context.registry()
    plan: dict[str, Any] | None = None
    plan_file: Path | None = None
    try:
        scope, target_id, target_path = _resolve_plan_target(registry, args)
        project_root = Path(args.project) if args.project else None
        decision = decide_scope(
            ScopeRequest(
                capability_id=args.capability,
                project_root=project_root,
                intent="install",
                creates_environment=args.creates_environment,
                requires_cuda_or_native=args.requires_cuda_or_native,
                declared_size_bytes=args.size_bytes,
                source_verifiable=not args.source_unverifiable,
            )
        )
        routing = {
            "requested_scope": scope,
            "decided_scope": decision.scope,
            "origin": decision.origin,
            "confirmation_required": decision.confirmation_required,
            "target_id": target_id,
            "target_path": target_path,
            "size_estimate_bytes": decision.size_estimate_bytes,
            "size_source": decision.size_source,
            "threshold_bytes": decision.threshold_bytes,
            "manifest_hits": list(decision.manifest_hits),
            "decision_reason": decision.reason_code,
        }
        if decision.reason_code == "CAPABILITY_NOT_DECLARED":
            raise AirootError(
                "CAPABILITY_NOT_DECLARED",
                f"no frozen capability is declared for {args.capability}",
                evidence=[item["detail"] for item in decision.evidence],
            )

        # Widening needs approval; narrowing yourself does not (draft §20.3-4).
        upgrading = scope == "data-root" and decision.scope == SCOPE_PROJECT

        if args.dry_run:
            document = {
                "schema_version": 1,
                "operation": "plan_dry_run",
                "capability_id": args.capability,
                "target": {"scope": scope, "data_root_id": target_id, "path": target_path},
                "routing": routing,
                "confirmation_required": decision.confirmation_required,
                "options": list(decision.options),
                "required_approval": "scope_upgrade" if upgrading else ("scope_confirmation" if decision.confirmation_required else "none"),
                "side_effects": ["writes_store", "writes_registry", "derived_cache"],
                "version": args.version,
                "reason_code": (
                    "SCOPE_UPGRADE_REQUIRES_APPROVAL"
                    if upgrading
                    else ("SCOPE_CONFIRMATION_REQUIRED" if decision.confirmation_required else "SUCCESS")
                ),
            }
            document["size_estimate_bytes"] = decision.size_estimate_bytes
            document["size_source"] = decision.size_source
            if decision.size_estimate_bytes is None:
                document["size_note"] = "SIZE_ESTIMATE_UNAVAILABLE"
            _emit(
                document,
                as_json=args.json,
                lines=[
                    f"dry run: {args.capability} -> scope={scope} target={target_path}",
                    f"  routing: decided={decision.scope} origin={decision.origin}",
                    f"  size: {decision.size_estimate_bytes if decision.size_estimate_bytes is not None else 'unknown (SIZE_ESTIMATE_UNAVAILABLE)'}",
                    *(
                        ["  confirmation required; options: " + " / ".join(decision.options)]
                        if decision.confirmation_required
                        else []
                    ),
                    *(["  scope upgrade to data-root requires separate approval"] if upgrading else []),
                    "  nothing was written",
                ],
            )
            return document, exit_code_for(document["reason_code"])

        if upgrading:
            raise AirootError(
                "SCOPE_UPGRADE_REQUIRES_APPROVAL",
                "a project-owned capability may not be widened to the data root without approval",
                evidence=[
                    f"the project manifests reference {args.capability}, so its scope is project",
                    "run with --dry-run to see the plan, then approve the scope upgrade explicitly",
                ],
            )
        if decision.confirmation_required:
            # Deliberately no plan file: an unconfirmed plan on disk could be approved by another
            # path, and "not decided yet" must be provable on the filesystem too (draft §20.3-2).
            raise AirootError(
                "SCOPE_CONFIRMATION_REQUIRED",
                f"installing {args.capability} needs confirmation before a plan may be produced",
                evidence=[
                    *[item["detail"] for item in decision.evidence if item["kind"] == "high_risk"],
                    "options: " + " / ".join(decision.options),
                    f"origin={decision.origin}",
                ],
            )

        if args.source_json:
            # A real artifact plan: the digest comes from the resolved source document, which got
            # it from a published checksum file — never from the artifact itself (draft §23.3-1).
            from .caps.backends import resolve_backend
            from .tx.artifact import create_artifact_plan

            source_document = json.loads(Path(args.source_json).read_text(encoding="utf-8"))
            backend = resolve_backend(str(source_document.get("backend_id") or "https_artifact"), root=context.path())
            plan = create_artifact_plan(
                registry,
                backend,
                capability_id=args.capability,
                version=args.version,
                kind="managed_tool",
                locator=str(source_document["artifact_url"]),
                source_digest=str(source_document["expected_digest"]),
                clock=context.clock,
                ttl_minutes=args.ttl_minutes,
                requested_by=args.requested_by,
            )
            if source_document.get("provenance"):
                plan["source"]["provenance"] = {
                    "source_id": source_document["provenance"]["source_id"],
                    "publisher": source_document["provenance"].get("publisher"),
                }
            plan["metadata"]["source_catalog"] = {
                "checksum_url": source_document.get("checksum_url"),
                "checksum_format": source_document.get("checksum_format"),
                "offline": source_document.get("offline", False),
                "provenance_and_integrity_are_separate": True,
                "signature": None,
            }
        else:
            plan = create_plan(
                registry,
                version=args.version,
                clock=context.clock,
                ttl_minutes=args.ttl_minutes,
                requested_by=args.requested_by,
                capability_id=args.capability,
            )
        plan["metadata"] = {**plan.get("metadata", {}), "routing": routing}
        plan["plan_hash"] = plan_hash(plan)
        validate_self("plan", plan)
        plan_file = _plan_path(context, plan["plan_id"])
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        registry.close()

    assert plan is not None and plan_file is not None
    plan["plan_file"] = str(plan_file)
    plan["reason_code"] = "SUCCESS"
    _emit(
        plan,
        as_json=args.json,
        lines=[
            f"plan {plan['plan_id']} -> {plan_file}",
            f"  hash {plan['plan_hash']}",
            f"  routing: decided={plan['metadata']['routing']['decided_scope']} "
            f"target={plan['metadata']['routing']['target_path']}",
        ],
    )
    return plan, EXIT_SUCCESS


def cmd_approve(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    plan = _load_plan(args.plan_file)
    token = _load_token(args.token_file)
    registry = context.registry()
    try:
        verify_approval(registry, plan, token, keyring=load_keyring(context.path()), clock=context.clock)
        record_approval(registry, token)
    finally:
        registry.close()
    document = {
        "schema_version": 1,
        "approval_id": token["approval_id"],
        "plan_hash": token["plan_hash"],
        "approval_mode": token["approval_mode"],
        "issuer": token["issuer"],
        "reason_code": "SUCCESS",
    }
    _emit(document, as_json=args.json, lines=[f"approval {token['approval_id']} accepted for consumption"])
    return document, EXIT_SUCCESS


def _runner_for(registry: Any, plan: dict[str, Any], context: Context) -> Any:
    """Pick the transaction driver by the backend the plan was built with (draft §21.5).

    ``fake_fixture`` keeps the original simulation runner, because the golden corpus and the
    existing journal tests are byte-for-byte pinned to it. Every other backend goes through the
    artifact runner: same states, same journal, different bytes.
    """

    from .caps.backends import resolve_backend
    from .tx.artifact import ArtifactRunner

    metadata = plan.get("metadata") or {}
    backend_id = metadata.get("backend_id") or metadata.get("backend") or "fake_fixture"
    if backend_id == "fake_fixture":
        return SimulationRunner(registry, clock=context.clock)
    return ArtifactRunner(
        registry, resolve_backend(str(backend_id), root=context.path()), clock=context.clock
    )


def cmd_install(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    plan = _load_plan(args.plan_file)
    token = _load_token(args.token_file)
    registry = context.registry()
    try:
        runner = _runner_for(registry, plan, context)
        tx = runner.commit(plan, token)
    finally:
        registry.close()

    state = str(tx["state"])
    if tx.get("interrupted"):
        reason = "PENDING_TRANSACTION"
    elif state == "FINALIZED":
        reason = "SUCCESS"
    else:
        reason = str(tx.get("outcome") or "BROKEN")
    tx["reason_code"] = reason
    _emit(
        tx,
        as_json=args.json,
        lines=[f"transaction {tx['transaction_id']}: {state} ({reason})", f"  generation {tx['generation_before']} -> {tx['generation_after']}"],
    )
    return tx, exit_code_for(reason)


def cmd_repair(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    registry = context.registry()
    try:
        if args.tx:
            targets = [args.tx]
        else:
            targets = [str(row["transaction_id"]) for row in registry.transactions(unfinished_only=True)]
        results = []
        for transaction_id in targets:
            # Repair must drive the *same* backend that created the transaction, or a resume
            # would re-materialise different bytes than the plan authorised.
            row = registry.transaction(transaction_id) if hasattr(registry, "transaction") else None
            stored_plan = None
            if row is not None:
                try:
                    stored_plan = json.loads(row["payload_json"]).get("plan")
                except (ValueError, TypeError):
                    stored_plan = None
            if stored_plan is None:
                result = repair(registry, transaction_id, clock=context.clock)
            else:
                runner = _runner_for(registry, stored_plan, context)
                result = {
                    "transaction_id": transaction_id,
                    "action": "resumed",
                    "state": runner.resume(transaction_id).get("state"),
                }
            results.append(result)
    finally:
        registry.close()
    document = {
        "schema_version": 1,
        "repaired": results,
        "reason_code": "SUCCESS",
        "action": results[0]["action"] if results else "no_action",
    }
    _emit(
        document,
        as_json=args.json,
        lines=[f"repair: {len(results)} transaction(s)"] + [f"  {item['transaction_id']} -> {item['action']}" for item in results],
    )
    return document, EXIT_SUCCESS


# --------------------------------------------------------------------------- #
# search (draft §31): protocol surface over a bounded crawl
# --------------------------------------------------------------------------- #

SEARCH_RESERVED = ("status", "explain", "implementations", "refresh", "rebuild")

SEARCH_EXTENSION_ID = "airoot-native-search-extension"
SEARCH_IMPLEMENTATION_ID = "airoot-native-search-crawl"


def _claim_spelling(path: str) -> str:
    """The spelling a registry path *claim* is compared in.

    Every writer canonicalizes (`paths.canonicalize` resolves) and so does the crawl (`caps/search.py`
    `_canonical_root`), so the two sides of this comparison normally hold the same string already.
    They are not guaranteed to: a caller can register a directory through an 8.3 alias
    (`C:\\Users\\PROFIL~1\\…`) or spell it with a `.`/`..` segment, and the registry row then carries a
    spelling the crawl's recorded paths never do. Measured (draft §111): with such a row the
    classifier answered `unmanaged` for paths **inside** a registered reference, and `--managed-only`
    then hid real matches — a silent wrong answer rather than a visible failure. Resolving the claim
    costs one call per registry row, not one per search result, and `resolve()` on a missing tail is a
    no-op rather than an error (it is not `strict`).
    """

    try:
        return str(Path(path).resolve())
    except OSError:  # pragma: no cover - resolve() is best-effort by design
        return path


def _search_classifier(registry: Any, context: Context) -> Any:
    """Build a path → (management, capability_id) tagger from a *snapshot* of declared state.

    A snapshot, not a live query: the crawl walks for seconds and the registry must not be held
    open (nor re-queried per result) while it does. Longest prefix wins, so a reference nested
    inside another object still gets its own tag.
    """

    from .paths import from_root_relative

    claims: list[tuple[str, str, str | None]] = []
    for row in registry.external_references():
        claims.append((_claim_spelling(str(row["path"])), "external_reference", row["capability_id"]))
    for row in registry.instances():
        try:
            store_dir = from_root_relative(str(row["store_path"]), context.path())
        except (KeyError, TypeError, ValueError):
            continue
        capability = row["capability_id"] if "capability_id" in row.keys() else None
        claims.append((_claim_spelling(str(store_dir)), "managed", capability))

    prepared = sorted(((path.lower().rstrip("\\") + "\\", management, capability) for path, management, capability in claims))

    def classify(path: str) -> tuple[str, str | None]:
        needle = str(path).lower()
        best: tuple[int, str, str | None] | None = None
        for prefix, management, capability in prepared:
            if needle + "\\" == prefix or needle.startswith(prefix):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), management, capability)
        if best is not None:
            return best[1], best[2]
        return "unmanaged", None

    return classify


def _canonical_data_roots(data_roots: list[str]) -> list[str]:
    """The data roots in the spelling `search` asks its coverage question with.

    `search` resolves them (`caps/search.py` `resolve_roots` → `_canonical_root`); `explain` asked
    with the raw registry strings, so the two commands could disagree about one index: a row spelled
    through an alias made `explain` predict a live crawl while `search` answered from the index
    (draft §111 — measured, and the reason this is one function now). A root that cannot be resolved
    is kept as it is rather than dropped: `explain` is a *prediction*, and quietly predicting over
    fewer roots would be a second way for the two to disagree.
    """

    from .caps import search as search_caps

    resolved: list[str] = []
    for candidate in data_roots:
        try:
            roots, _origin = search_caps.resolve_roots([candidate])
        except AirootError:
            resolved.append(candidate)
            continue
        resolved.extend(roots)
    return resolved


def cmd_search(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    """Find files by name — over a bounded crawl, and say so (draft §31, ADR-0017).

    The reserved words are recognised only as the *first positional*, exactly as the protocol
    requires: `airoot search status` manages the index, `airoot search --query status` searches for
    a file named `status`. Nothing here guesses from context.
    """

    from .caps import search as search_caps

    tokens = list(args.query_tokens or [])
    if len(tokens) > 1 and tokens[0] != "explain":
        raise AirootError(
            "INVALID_INPUT",
            "search takes one query",
            evidence=[
                "quote a query that contains spaces",
                "airoot search explain <query> --json",
            ],
        )
    if args.query_option is None and tokens[:1] and tokens[0] in SEARCH_RESERVED:
        return _search_reserved(tokens[0], tokens[1:], args, context)
    if args.query_option is not None and tokens:
        raise AirootError(
            "INVALID_INPUT",
            "give the query either positionally or with --query, not both",
            evidence=["airoot search <query>", "airoot search --query <text>"],
        )
    query = args.query_option if args.query_option is not None else (tokens[0] if tokens else None)
    if query is None:
        raise AirootError(
            "INVALID_INPUT",
            "search needs something to look for",
            evidence=[
                "airoot search <query> --json",
                "airoot search --query status --json  # to search for a file named 'status'",
            ],
        )

    policy = search_caps.load_search_policy()
    request = search_caps.build_request(
        query,
        policy=policy,
        match=args.match,
        target=args.target,
        roots=args.search_roots,
        extensions=args.extensions,
        include_directories=args.include_directories,
        accessible_only=args.accessible_only,
        include_hidden=args.include_hidden,
        allow_reparse_points=args.allow_reparse_points,
        min_size=args.min_size,
        max_size=args.max_size,
        modified_after=args.modified_after,
        limit=args.limit,
        max_duration_ms=args.max_duration_ms,
        cursor=args.cursor,
        consistency=args.consistency,
        max_staleness_ms=args.max_staleness_ms,
    )

    registry = context.registry()
    try:
        data_roots = [str(row["path"]) for row in registry.data_roots()]
        classify = _search_classifier(registry, context)
    finally:
        registry.close()

    roots, origin = search_caps.resolve_roots(request["roots"], policy=policy, data_roots=data_roots)
    keep = (lambda record: record["management"] != "unmanaged") if args.managed_only else None

    document, code = search_caps.execute_search(
        request,
        roots,
        extension_id=SEARCH_EXTENSION_ID,
        implementation_id=SEARCH_IMPLEMENTATION_ID,
        policy=policy,
        classify=classify,
        keep=keep,
        index_root=context.path(),
    )
    document["evidence"].append({"kind": "search_scope", "detail": f"roots came from the {origin}"})
    # `--managed-only` is the caller's filter, so it is not part of what the crawl found; say how
    # many matches it removed rather than letting the count look like the whole truth.
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"{document['data']['stats']['returned']} of {document['data']['stats']['matched']} match(es)"
            f"  [{document['status']}] {document['reason_code']}",
            *[f"  {item['management']:<19} {item['path']}" for item in document["data"]["results"]],
            *[f"  note: {warning}" for warning in document["warnings"]],
            "  no index in this build: results come from a bounded crawl, not from a healthy index",
        ],
    )
    return document, code


def _search_reserved(
    token: str, rest: list[str], args: argparse.Namespace, context: Context
) -> tuple[dict[str, Any], int]:
    """`status` / `explain` / `implementations` / `refresh` (`rebuild` is its alias)."""

    from .caps import search as search_caps
    from .caps import searchindex
    from .caps import usn as usn_caps
    from .ext.manifest import declared_operations, load_manifests

    policy = search_caps.load_search_policy()
    manifests = load_manifests()
    candidates = [
        {
            "extension_id": manifest["extension_id"],
            "implementation_id": manifest["implementation_id"],
            "implementation_kind": manifest["implementation_kind"],
            "capability_types": list(manifest.get("capability_types", [])),
            "operations": sorted(declared_operations(manifest)),
            "required_privilege": manifest["required_privilege"],
            "side_effects": list(manifest.get("side_effects", [])),
            "available": True,
        }
        for manifest in manifests.values()
        if "file_search" in manifest.get("capability_types", [])
    ]
    candidates.append(usn_caps.native_candidate())

    if token == "implementations":
        document = {
            "schema_version": 1,
            "revision": policy.revision,
            "implementations": candidates,
            "reason_code": "SUCCESS" if candidates else "EXTENSION_NOT_FOUND",
        }
        _emit(
            document,
            as_json=args.json,
            lines=[
                f"{item['implementation_id']} ({item['implementation_kind']})"
                + ("" if item.get("available", True) else f"  [unavailable: {item['reason_code']}]")
                for item in candidates
            ]
            or ["no extension declares file_search"],
        )
        return document, exit_code_for(document["reason_code"])

    registry = context.registry()
    try:
        data_roots = [str(row["path"]) for row in registry.data_roots()]
    finally:
        registry.close()

    if token in ("refresh", "rebuild"):
        return _search_refresh(args, context, policy=policy, data_roots=data_roots)

    state = searchindex.read_state(context.path(), policy=policy)

    if token == "status":
        fresh, stale_reason = searchindex.freshness_for(
            state,
            # `status` has no request of its own, so it judges freshness against the policy default.
            {"max_staleness_ms": policy.default("max_staleness_ms", 30000)},
            clock=context.clock,
        )
        index_document = state.to_document()
        index_document["path"] = str(searchindex.index_path(context.path(), policy))
        if not state.readable:
            reason_code = "SEARCH_NOT_READY" if not state.present else "SEARCH_INDEX_DEGRADED"
        else:
            reason_code = stale_reason or "SUCCESS"
        document = {
            "schema_version": 1,
            "operation": "status",
            "implementation_id": SEARCH_IMPLEMENTATION_ID,
            "freshness": fresh,
            "index": index_document,
            "native_index": usn_caps.native_candidate(),
            "policy_revision": policy.revision,
            "data_roots": data_roots,
            "declared_operations": sorted(
                {name for item in candidates for name in item.get("operations", [])}
            ),
            "reason_code": reason_code,
        }
        if getattr(args, "probe_native_index", False):
            # Explicit opt-in: this is the only path that opens a volume handle (draft §34.3-2).
            probe = usn_caps.probe(query=True)
            document["native_index"]["probe"] = probe.to_document()
            document["native_index"]["checked"] = True
            document["native_index"]["available"] = probe.native_index_available
            document["native_index"]["reason"] = probe.reason
        _emit(
            document,
            as_json=args.json,
            lines=[
                f"implementation: {document['implementation_id']}",
                f"index: {'present' if state.present else 'absent'}"
                + (f", {state.records} record(s), built {state.built_at}" if state.readable else ""),
                f"freshness: {fresh['state']} / coverage {fresh['coverage']}"
                + (f" (lag {fresh['lag_ms']} ms)" if fresh["lag_ms"] is not None else ""),
                f"policy: {policy.revision}; data roots: {len(data_roots)}",
                f"native index: unavailable — {document['native_index']['reason']}",
                *(
                    [
                        "native index probe (read-only):",
                        *[f"  {item}" for item in document["native_index"]["probe"]["observations"]],
                    ]
                    if "probe" in document["native_index"]
                    else []
                ),
                *([f"problem: {state.problem}"] if state.problem else []),
            ],
        )
        return document, exit_code_for(reason_code)

    # explain: say which implementation would answer, and whether the index can serve the roots.
    selected = candidates[0] if candidates else None
    fresh, stale_reason = searchindex.freshness_for(
        state,
        {"max_staleness_ms": policy.default("max_staleness_ms", 30000)},
        clock=context.clock,
    )
    if selected is None:
        source, reason = None, "no extension declares file_search"
        reason_code = "EXTENSION_NOT_FOUND"
    elif state.present and not state.readable:
        source, reason = "crawl", f"the index is unusable ({state.problem}); a live crawl answers instead"
        reason_code = "SEARCH_INDEX_DEGRADED"
    elif not state.readable:
        source = "crawl"
        reason, reason_code = "no index has been built yet; a live crawl answers instead", "SEARCH_FALLBACK_USED"
    elif not state.covers(_canonical_data_roots(data_roots)):
        source = "crawl"
        reason = "the index does not cover the current data roots; a live crawl answers instead"
        reason_code = "SEARCH_FALLBACK_USED"
    else:
        source = "index"
        reason = f"index built {state.built_at} ({state.records} records, coverage {state.coverage})"
        reason_code = stale_reason or "SUCCESS"
    document = {
        "schema_version": 1,
        "operation": "explain",
        "query": rest[0] if rest else None,
        "selected": None if selected is None else selected["implementation_id"],
        "reason": reason,
        "would_answer_from": source,
        "freshness": fresh,
        "index": state.to_document(),
        "native_index": usn_caps.native_candidate(),
        "fallback": {
            "used": source == "crawl",
            "kind": "crawl" if source == "crawl" else None,
        },
        "candidates": [item["implementation_id"] for item in candidates],
        "reason_code": reason_code,
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"selected: {document['selected']}",
            f"reason: {document['reason']}",
            f"would answer from: {document['would_answer_from']}",
            f"freshness: {fresh['state']} / coverage {fresh['coverage']}",
            f"native index: unavailable ({document['native_index']['reason_code']}) — "
            f"{document['native_index']['probe_hint']}",
        ],
    )
    return document, exit_code_for(reason_code)


def _search_refresh(
    args: argparse.Namespace,
    context: Context,
    *,
    policy: Any,
    data_roots: list[str],
) -> tuple[dict[str, Any], int]:
    """Build (or rebuild) the crawl index for the policy roots — the only thing that writes here.

    `--rebuild` is accepted as the protocol spells it, and is honest about doing the same thing:
    this index has no incremental mode, every build is a full walk (draft §32.2-1, ADR-0019).
    """

    from .caps import search as search_caps
    from .caps import searchindex

    request_roots = list(getattr(args, "search_roots", []) or [])
    roots, origin = search_caps.resolve_roots(request_roots, policy=policy, data_roots=data_roots)
    build = searchindex.build_index(context.path(), roots=roots, policy=policy, clock=context.clock)

    reason_code = "SUCCESS" if build.coverage == "complete_for_roots" else "SEARCH_INDEX_DEGRADED"
    document: dict[str, Any] = {
        "schema_version": 1,
        "operation": "refresh",
        "implementation_id": SEARCH_IMPLEMENTATION_ID,
        "roots": list(build.roots),
        "roots_origin": origin,
        "records": build.records,
        "coverage": build.coverage,
        "built_at": build.built_at,
        "elapsed_ms": build.elapsed_ms,
        "truncated": build.truncated,
        "timed_out": build.timed_out,
        "unreadable_directories": build.unreadable_directories,
        "index_path": str(searchindex.index_path(context.path(), policy)),
        # The ADR-0019 boundary, stated in the answer: one derived file was replaced atomically and
        # not a single file inside a data root was touched.
        "index_replaced": True,
        "data_root_files_touched": 0,
        "reason_code": reason_code,
    }
    _emit(
        document,
        as_json=args.json,
        lines=[
            f"index built: {build.records} record(s) from {len(build.roots)} root(s)"
            f" in {build.elapsed_ms} ms [{build.coverage}]",
            f"  {document['index_path']}",
            "  no file inside a data root was touched; the index was replaced atomically",
            *([f"  note: the walk hit a bound, so coverage is partial"] if build.truncated else []),
        ],
    )
    return document, exit_code_for(reason_code)


# --------------------------------------------------------------------------- #
# loading helpers
# --------------------------------------------------------------------------- #


def _plan_path(context: Context, plan_id: str) -> Path:
    return context.path() / "state" / "plans" / f"{plan_id.replace('/', '_')}.json"


def _load_document(path: Path, schema: str, reason_code: str) -> dict[str, Any]:
    if not path.is_file():
        raise AirootError("INVALID_INPUT", f"file not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AirootError(reason_code, f"{path} is not readable JSON", evidence=[str(exc)]) from exc
    validate_document(schema, document, reason_code=reason_code)
    return document


def _load_plan(path: str | Path) -> dict[str, Any]:
    return _load_document(Path(path), "plan", "INVALID_PLAN")


def _load_token(path: str | Path) -> dict[str, Any]:
    return _load_document(Path(path), "approval-token", "INVALID_APPROVAL")


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


class _Parser(argparse.ArgumentParser):
    """Usage errors are invalid input (exit code 8), not argparse's default 2.

    Exit code 2 means "degraded or drift" in the frozen table, so letting argparse
    keep its default would report a typo as a state problem.
    """

    def error(self, message: str) -> Any:  # type: ignore[override]
        raise AirootError("INVALID_INPUT", message, evidence=[f"usage: {self.prog} --help"])


#: Verbs the planning document names and this build deliberately does not carry. The long reason
#: lives in `agents/airoot.json` (`deferred`), which the core must not read at run time: the Skill
#: layer is user-writable, and the protected broker the plan puts above it (规划 §8.1) must not
#: trust it. So the core carries the two facts a caller can *act* on -- the deferred category and
#: the word that unlocks it -- and `test_l0_consistency.py` holds the two copies equal in both
#: directions, so neither can drift without the other.
DECLARED_ABSENT: dict[tuple[str, ...], tuple[str, str]] = {
    ("bootstrap",): ("needs-admin", "p2-protected-state"),
    ("reconcile",): ("needs-capability", "p6-project-manifest"),
    ("path", "backup"): ("needs-admin", "p2-protected-state"),
    ("path", "restore"): ("needs-admin", "p2-protected-state"),
    ("root", "adopt"): ("needs-admin", "p2-protected-state"),
    ("root", "relocate"): ("needs-admin", "p2-protected-state"),
}

#: Global options that consume the token after them, so `airoot --root <dir> bootstrap` reads
#: `bootstrap` as the verb instead of as `--root`'s value.
_GLOBAL_VALUE_OPTIONS = ("--root",)


def declared_absent_verb(arguments: list[str]) -> tuple[str, str, str] | None:
    """The declared-absent verb this command line starts with, if there is one.

    Only the **leading** positional tokens are read, so `airoot where bootstrap` and
    `airoot adopt bootstrap` are not this: a capability or a path may legitimately be called
    `bootstrap`, and only the verb position decides.
    """

    positionals: list[str] = []
    index = 0
    while index < len(arguments) and len(positionals) < 2:
        token = arguments[index]
        if token in _GLOBAL_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1

    for depth in (2, 1):
        path = tuple(positionals[:depth])
        if path in DECLARED_ABSENT:
            category, unlock = DECLARED_ABSENT[path]
            return " ".join(path), category, unlock
    return None


def _declared_absent_error(verb: str, category: str, unlock: str) -> AirootError:
    """The refusal for a verb that exists in the plan and not in this build (ADR-0027).

    Structured rather than prose-only: `details` carries the two facts an agent can branch on, so
    the caller does not have to parse the sentence, and the audit guard can compare them with
    `agents/airoot.json` verb by verb.
    """

    return AirootError(
        "NOT_IMPLEMENTED",
        f"{verb} is named by the planning document and deliberately absent from this build",
        evidence=[
            f"deferred category: {category}",
            f"unlocks when: {unlock}",
            f"why: agents/airoot.json -> deferred -> '{verb}'",
        ],
        details={"command": verb, "deferred_category": category, "unblocked_by": unlock},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="airoot",
        description="AIROOT local capability control plane (P1, protocol level)",
    )
    parser.add_argument("--root", help="AIROOT root directory (defaults to AIROOT_HOME)")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable document")

    # The documented form puts these after the subcommand (`airoot doctor --json`),
    # while argparse only knows the main parser's copies. SUPPRESS keeps the
    # subparser from clobbering a value already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", required=True)

    root_parser = subparsers.add_parser("root", help="root identity commands", parents=[common])
    root_sub = root_parser.add_subparsers(dest="subcommand", required=True)
    root_sub.add_parser("status", help="report the resolved root identity", parents=[common])

    where_parser = subparsers.add_parser("where", help="resolve a capability", parents=[common])
    where_parser.add_argument("capability")
    where_parser.add_argument("--version", help="version constraint, e.g. '>=3.11,<3.13'")
    where_parser.add_argument("--scope", choices=["system", "machine", "session", "project"])
    where_parser.add_argument("--session-id")
    where_parser.add_argument("--project-id")
    where_parser.add_argument("--allow-external-fallback", action="store_true")

    doctor_parser = subparsers.add_parser("doctor", help="check invariants D1-D10", parents=[common])
    doctor_parser.add_argument("--verify", action="store_true", help="re-hash store payloads and reference entrypoints")
    doctor_parser.add_argument(
        "--include-unmanaged",
        action="store_true",
        help=(
            "also report recorded objects that match no frozen capability (info level); "
            "run 'discover --record' first, doctor never scans data roots on its own"
        ),
    )

    inventory_parser = subparsers.add_parser("inventory", help="declared-state projection", parents=[common])
    inventory_parser.add_argument("--scope", choices=["system", "machine", "session", "project"])
    inventory_parser.add_argument("--class", dest="klass", choices=[
        "managed_tool", "runtime", "external_reference", "unmanaged", "project_owned", "quarantined"
    ])

    extension_parser = subparsers.add_parser("extension", help="capability extensions", parents=[common])
    extension_sub = extension_parser.add_subparsers(dest="subcommand", required=True)
    extension_sub.add_parser("list", parents=[common])
    extension_status = extension_sub.add_parser("status", parents=[common])
    extension_status.add_argument("extension_id")
    extension_status.add_argument(
        "--operation", choices=["status", "probe", "invoke"], default="status",
        help="which declared operation to run (default: status)",
    )

    plan_parser = subparsers.add_parser(
        "plan", help="create a canonical plan (P1: simulated source only)", parents=[common]
    )
    plan_parser.add_argument("capability")
    plan_parser.add_argument("--version", default="1.0.0")
    plan_parser.add_argument("--ttl-minutes", type=int, default=5)
    plan_parser.add_argument("--requested-by", default="airoot-cli")
    # Routing (draft §12.4): where this capability belongs, and whether a human must decide.
    plan_parser.add_argument(
        "--scope", choices=["machine", "project", "data-root"], default=None,
        help="the destination class; data-root is the global one (draft §12.1)",
    )
    plan_parser.add_argument("--target", default=None, help="<dir> for project, data-root:<id> for data-root")
    plan_parser.add_argument("--project", default=None, help="project root whose manifests decide 'project'")
    plan_parser.add_argument("--dry-run", action="store_true", help="route and report, write nothing")
    plan_parser.add_argument("--creates-environment", action="store_true")
    plan_parser.add_argument("--requires-cuda-or-native", action="store_true")
    plan_parser.add_argument("--size-bytes", type=int, default=None, help="a size the caller actually knows")
    plan_parser.add_argument("--source-unverifiable", action="store_true")
    plan_parser.add_argument(
        "--source-json", default=None,
        help="a resolved source document from 'source resolve': builds a real artifact plan",
    )

    approve_parser = subparsers.add_parser("approve", help="consume a protected approval token", parents=[common])
    approve_parser.add_argument("plan_file")
    approve_parser.add_argument("--token-file", required=True)

    install_parser = subparsers.add_parser("install", help="run the simulated controlled transaction", parents=[common])
    install_parser.add_argument("plan_file")
    install_parser.add_argument("--token-file", required=True)

    repair_parser = subparsers.add_parser("repair", help="journal-driven recovery", parents=[common])
    repair_parser.add_argument("--tx")

    rebuild_parser = subparsers.add_parser(
        "rebuild", help="regenerate derived state from the registry (draft §24)", parents=[common]
    )
    rebuild_parser.add_argument(
        "--plan", action="store_true", help="report what would be rewritten, write nothing"
    )

    # --- steward domain (ADR-0004) ------------------------------------------ #
    data_root_parser = subparsers.add_parser(
        "data-root", help="declare directories AIROOT observes but never owns", parents=[common]
    )
    data_root_sub = data_root_parser.add_subparsers(dest="subcommand", required=True)

    data_root_add = data_root_sub.add_parser("add", help="register a data root", parents=[common])
    data_root_add.add_argument("path")
    data_root_add.add_argument("--role", choices=["runtime", "tool", "mixed"], default="mixed")
    data_root_add.add_argument("--id", dest="data_root_id", help="override the derived data_root_id")

    data_root_list = data_root_sub.add_parser("list", help="list registered data roots", parents=[common])
    data_root_list.add_argument("--all", action="store_true", help="include inactive roots")

    data_root_forget = data_root_sub.add_parser(
        "forget", help="unregister a data root and its observations (never its files)", parents=[common]
    )
    data_root_forget.add_argument("data_root_id")

    discover_parser = subparsers.add_parser(
        "discover", help="read-only classification of registered data roots", parents=[common]
    )
    discover_parser.add_argument("--data-root", help="limit the scan to one registered data root")
    discover_parser.add_argument(
        "--record",
        action="store_true",
        help="persist unmanaged/quarantined observations (never adopts anything)",
    )

    adopt_parser = subparsers.add_parser("adopt", help="record a reference to an existing object", parents=[common])
    adopt_parser.add_argument("path")
    adopt_parser.add_argument("--mode", choices=["reference", "import", "recreate"], default="reference")
    adopt_parser.add_argument(
        "--capability", default=None, help="import: the capability this file provides (not discoverable)"
    )
    adopt_parser.add_argument(
        "--version", default=None, help="import: the version to record (a loose file states none)"
    )

    forget_parser = subparsers.add_parser(
        "forget", help="drop AIROOT's record of a reference (never its files)", parents=[common]
    )
    forget_parser.add_argument("external_id")

    unadopt_parser = subparsers.add_parser(
        "unadopt", help="compatibility alias of 'forget' (draft §8)", parents=[common]
    )
    unadopt_parser.add_argument("external_id")

    scope_parser = subparsers.add_parser(
        "scope", help="decide where a capability belongs (asks, never installs)", parents=[common]
    )
    scope_sub = scope_parser.add_subparsers(dest="subcommand", required=True)

    scope_decide = scope_sub.add_parser("decide", help="route one capability", parents=[common])
    scope_decide.add_argument("capability")
    scope_decide.add_argument("--project", help="project root whose manifests decide 'project-isolated'")
    scope_decide.add_argument("--intent", choices=["install", "reference"], default="install")
    scope_decide.add_argument(
        "--creates-environment", action="store_true", help="the request would build an environment"
    )
    scope_decide.add_argument(
        "--requires-cuda-or-native", action="store_true", help="needs CUDA or non-Python binaries"
    )
    scope_decide.add_argument(
        "--size-bytes", type=int, default=None,
        help="a size the caller actually knows; omitted means unknown and is never guessed",
    )
    scope_decide.add_argument(
        "--source-unverifiable", action="store_true", help="source/integrity cannot be verified"
    )

    scope_memory = scope_sub.add_parser("memory", help="show recorded routing choices", parents=[common])
    scope_memory.add_argument("--project", required=True)

    env_parser = subparsers.add_parser(
        "env", help="session activation and persisted environment (draft §13)", parents=[common]
    )
    env_sub = env_parser.add_subparsers(dest="subcommand", required=True)

    env_activate = env_sub.add_parser(
        "activate", help="print shell code for this session only (writes nothing)", parents=[common]
    )
    env_activate.add_argument("external_id")
    env_activate.add_argument("--shell", choices=["powershell", "cmd"], default="powershell")
    env_activate.add_argument(
        "--session", default=None,
        help="record a snapshot stack under this id (injected, never invented); enables deactivate",
    )

    env_deactivate = env_sub.add_parser(
        "deactivate", help="pop the session activation stack and print the restore script", parents=[common]
    )
    env_deactivate.add_argument("--session", default=None)
    env_deactivate.add_argument("--shell", choices=["powershell", "cmd"], default="powershell")
    env_deactivate.add_argument("--all", action="store_true", help="clear the whole stack")

    env_list = env_sub.add_parser("list", help="persisted variables AIROOT has written", parents=[common])
    env_list.add_argument("--all", action="store_true", help="include variables already forgotten")

    env_persist = env_sub.add_parser(
        "persist", help="persist a reference's environment (needs approval)", parents=[common]
    )
    env_persist.add_argument("external_id", nargs="?", default=None)
    env_persist.add_argument("--scope", choices=["user", "machine"], default="user")
    env_persist.add_argument("--plan-file", default=None, help="commit a plan built earlier")
    env_persist.add_argument("--token-file", default=None, help="approval token bound to the plan hash")
    env_persist.add_argument("--plan-id", default=None)
    env_persist.add_argument("--requested-by", default="cli")
    env_persist.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")

    env_forget = env_sub.add_parser(
        "forget",
        help="restore the values AIROOT replaced",
        description=(
            "Restore what AIROOT persisted: give one reference's id, or --all to put every "
            "variable AIROOT ever wrote back the way it was (draft §13.4)."
        ),
        parents=[common],
    )
    env_forget.add_argument("external_id", nargs="?", default=None)
    env_forget.add_argument("--all", action="store_true", help="restore everything AIROOT ever persisted")
    env_forget.add_argument("--variable", default=None, help="only this variable")
    env_forget.add_argument(
        "--dry-run", action="store_true", help="report what would be restored, change nothing"
    )

    exec_parser = subparsers.add_parser(
        "exec",
        help="run one command with a reference's environment (session only)",
        description=(
            "Run one child process with a reference's environment injected. "
            "`exec --env <external-id> -- <command>` is accepted as an alias of the positional form "
            "(planning §15.1 spells it that way); options must come before `--`."
        ),
        parents=[common],
    )
    exec_parser.add_argument("external_id")
    # Named `child_command`: a positional called `command` would overwrite the
    # top-level `command` attribute argparse uses to pick the handler.
    exec_parser.add_argument("child_command", nargs=argparse.REMAINDER)

    search_parser = subparsers.add_parser(
        "search",
        help="find files by name (bounded crawl; this build ships no index)",
        description=(
            "Find files by name. `status`/`explain`/`implementations` are reserved words and are "
            "recognised only as the first argument: `airoot search status` manages the index while "
            "`airoot search --query status` searches for a file named 'status'. --search-root is "
            "the *search* root; --root is the AIROOT root. There is no resident index in this "
            "build, so every answer is a bounded crawl reported as degraded (draft §31)."
        ),
        parents=[common],
    )
    search_parser.add_argument(
        "query_tokens",
        nargs="*",
        metavar="query|status|explain|implementations",
        help="what to look for (or one of the reserved words)",
    )
    search_parser.add_argument("--query", dest="query_option", help="the text to look for")
    search_parser.add_argument("--match", choices=["exact", "prefix", "contains"])
    search_parser.add_argument("--target", choices=["name", "path", "name_and_path"])
    search_parser.add_argument(
        "--search-root",
        dest="search_roots",
        action="append",
        default=[],
        metavar="DIR",
        help="root to search (repeatable); defaults to the registered data roots",
    )
    search_parser.add_argument("--ext", dest="extensions", action="append", default=[], metavar=".EXE")
    search_parser.add_argument("--limit", type=int)
    search_parser.add_argument("--max-duration-ms", type=int)
    search_parser.add_argument("--max-staleness-ms", type=int)
    search_parser.add_argument("--cursor", help="continue a previous page (bound to the query)")
    search_parser.add_argument("--consistency", choices=["best_effort", "bounded_staleness", "refresh_then_read", "physical_verify"])
    search_parser.add_argument("--min-size", type=int)
    search_parser.add_argument("--max-size", type=int)
    search_parser.add_argument("--modified-after")
    search_parser.add_argument("--include-directories", action="store_true", default=None)
    search_parser.add_argument("--include-hidden", action="store_true", default=None)
    search_parser.add_argument("--allow-reparse-points", action="store_true", default=None)
    search_parser.add_argument(
        "--include-inaccessible",
        dest="accessible_only",
        action="store_false",
        default=None,
        help="also return entries this process cannot read (default: only accessible ones)",
    )
    search_parser.add_argument(
        "--managed-only",
        action="store_true",
        help="only paths AIROOT knows (a reference or an owned payload)",
    )
    search_parser.add_argument(
        "--probe-native-index",
        action="store_true",
        help=(
            "`search status` only: read a volume handle once to explain why the native (USN) index "
            "is unavailable. Without this flag no volume is touched (draft §34)"
        ),
    )

    tool_parser = subparsers.add_parser(
        "tool", help="retire and collect AIROOT-owned payloads (draft §14)", parents=[common]
    )
    tool_sub = tool_parser.add_subparsers(dest="subcommand", required=True)

    tool_retire = tool_sub.add_parser(
        "retire", help="clear the active binding; the payload stays", parents=[common]
    )
    tool_retire.add_argument("target", help="capability id or instance id")

    tool_list = tool_sub.add_parser("list", help="every AIROOT-owned instance", parents=[common])
    tool_list.add_argument("--capability", default=None, help="restrict to one capability")

    tool_status = tool_sub.add_parser(
        "status", help="read and verify one instance (never switches active)", parents=[common]
    )
    tool_status.add_argument("target", help="capability id or instance id")

    tool_verify = tool_sub.add_parser(
        "verify", help="digest/entrypoint verification (never adopts, never repairs)", parents=[common]
    )
    tool_verify.add_argument("target", help="capability id or instance id")

    tool_pin = tool_sub.add_parser(
        "pin", help="record desired state and offer a plan (never changes the binding)", parents=[common]
    )
    tool_pin.add_argument("capability")
    tool_pin.add_argument("--version", default=None, help="a constraint like '>=3.11,<3.13' or '1.0.0'")
    tool_pin.add_argument("--scope", choices=["machine", "project", "session"], default="machine")
    tool_pin.add_argument("--clear", action="store_true", help="remove the pin")
    tool_pin.add_argument(
        "--offline-checksum", default=None,
        help="a local checksum file, so a real-artifact plan can be built without network",
    )
    tool_pin.add_argument("--requested-by", default="airoot-cli")

    tool_gc = tool_sub.add_parser("gc", help="list or collect retired payloads", parents=[common])
    tool_gc.add_argument("--plan", dest="apply", action="store_false", default=False)
    tool_gc.add_argument("--apply", dest="apply", action="store_true")
    tool_gc.add_argument("--instance", default=None, help="restrict the plan to one instance")
    tool_gc.add_argument("--plan-file", default=None, help="apply a specific plan")
    tool_gc.add_argument("--token-file", default=None, help="approval token bound to the plan hash")
    tool_gc.add_argument("--requested-by", default="airoot-cli")

    uninstall_parser = subparsers.add_parser(
        "uninstall", help="retire then collect an owned payload (needs approval)", parents=[common]
    )
    uninstall_parser.add_argument("target", help="capability id or instance id")
    uninstall_parser.add_argument("--token-file", default=None)
    uninstall_parser.add_argument("--dry-run", action="store_true", help="retire and print the plan")

    path_parser = subparsers.add_parser(
        "path", help="inspect the PATH exposure invariant (draft §26)", parents=[common]
    )
    path_sub = path_parser.add_subparsers(dest="subcommand", required=True)
    path_sub.add_parser("verify", help="check the frozen PATH rule (read-only)", parents=[common])

    capability_parser = subparsers.add_parser(
        "capability", help="the frozen capability list and its admission rules (draft §15)", parents=[common]
    )
    capability_sub = capability_parser.add_subparsers(dest="subcommand", required=True)
    capability_sub.add_parser("list", help="show every frozen capability", parents=[common])
    capability_check = capability_sub.add_parser(
        "check", help="may AIROOT manage this object? (read-only)", parents=[common]
    )
    capability_check.add_argument("path")
    capability_check.add_argument("--capability", default=None, help="skip detection and name the capability")
    capability_check.add_argument(
        "--source-unverifiable", action="store_true", help="source/integrity cannot be verified"
    )
    capability_check.add_argument(
        "--irreversible", action="store_true", help="managing it would need an irreversible side effect"
    )

    source_parser = subparsers.add_parser(
        "source", help="trusted sources and published checksums (draft §23)", parents=[common]
    )
    source_sub = source_parser.add_subparsers(dest="subcommand", required=True)
    source_sub.add_parser("list", help="show the trusted source catalog", parents=[common])
    source_resolve = source_sub.add_parser(
        "resolve", help="(capability, version) -> artifact + published digest", parents=[common]
    )
    source_resolve.add_argument("capability")
    source_resolve.add_argument("--version", required=True)
    source_resolve.add_argument(
        "--offline-checksum", default=None,
        help="a local checksum file instead of fetching it (tests and air-gapped machines)",
    )
    source_resolve.add_argument("--source-out", default=None, help="write the resolved source here")

    return parser


COMMANDS: dict[str, Callable[[argparse.Namespace, Context], tuple[dict[str, Any], int]]] = {
    "root.status": cmd_root_status,
    "where": cmd_where,
    "doctor": cmd_doctor,
    "inventory": cmd_inventory,
    "extension.list": cmd_extension_list,
    "extension.status": cmd_extension_status,
    "plan": cmd_plan,
    "approve": cmd_approve,
    "install": cmd_install,
    "repair": cmd_repair,
    # Derived state can be rebuilt; authority cannot (draft §24).
    "rebuild": cmd_rebuild,
    # Steward domain (ADR-0004): observe, classify and record without owning.
    "data-root.add": cmd_data_root_add,
    "data-root.list": cmd_data_root_list,
    "data-root.forget": cmd_data_root_forget,
    "discover": cmd_discover,
    "adopt": cmd_adopt,
    "forget": cmd_forget,
    # `unadopt` is the frozen name from 规划 §15.1; `forget` is the clearer one (draft §8).
    "unadopt": cmd_forget,
    # Dependency routing (draft §12): decide, never install.
    "scope.decide": cmd_scope_decide,
    "scope.memory": cmd_scope_memory,
    # Environment exposure (draft §13): session code, or an approved persisted write.
    "env.activate": cmd_env_activate,
    "env.deactivate": cmd_env_deactivate,
    "env.list": cmd_env_list,
    "env.persist": cmd_env_persist,
    "env.forget": cmd_env_forget,
    "exec": cmd_exec,
    # Deletion semantics graded by ownership (draft §14).
    "tool.retire": cmd_tool_retire,
    "tool.list": cmd_tool_list,
    "tool.status": cmd_tool_status,
    "tool.verify": cmd_tool_verify,
    "tool.pin": cmd_tool_pin,
    "tool.gc": cmd_tool_gc,
    "path.verify": cmd_path_verify,
    "uninstall": cmd_uninstall,
    # Capability boundary (draft §15): what may be managed at all.
    "capability.list": cmd_capability_list,
    "capability.check": cmd_capability_check,
    # Trusted sources and published checksums (draft §23).
    "source.list": cmd_source_list,
    "source.resolve": cmd_source_resolve,
    # Search protocol surface (draft §31): one entry point, reserved words as first positional.
    "search": cmd_search,
}


def dispatch(args: argparse.Namespace, context: Context) -> tuple[dict[str, Any], int]:
    subcommand = getattr(args, "subcommand", None)
    key = args.command if subcommand is None else f"{args.command}.{subcommand}"
    handler = COMMANDS.get(key)
    if handler is None:
        raise AirootError("INVALID_INPUT", f"unknown command: {key}", evidence=sorted(COMMANDS))
    return handler(args, context)


def _command_token_index(arguments: list[str]) -> int | None:
    """Index of the top-level command token, or None.

    Only needed to recognise `exec` before argparse runs; `--root` is the one global option that
    takes a value, so it is the only pair that has to be skipped.
    """

    position = 0
    while position < len(arguments):
        token = arguments[position]
        if token == "--root":
            position += 2
            continue
        if token.startswith("-"):
            position += 1
            continue
        return position
    return None


#: The one flag spelling the frozen planning table (§15.1, §16.4) uses for `exec`
#: (`exec --env <id> -- <cmd>`), rewritten into the positional form by `_normalize_exec_argv`.
#: Declared as a constant rather than spelled inline twice so the documented surface and the L0
#: audit read the same source: a flag an agent may legitimately type must be discoverable from code.
EXEC_ALIAS_FLAG = "--env"


def _normalize_exec_argv(arguments: list[str]) -> list[str]:
    """Canonicalise `exec` before argparse sees it.

    `exec` is the one command that dispatches a child process, and it is the one command whose
    spelling the documents disagree about: the frozen planning table (§15.1, §16.4) writes
    `exec --env <name> -- <command>`, while the steward model passes the same identifier
    positionally (`exec <external-reference-id> -- <command>`). Both mean one thing, so the flag
    form is rewritten into the positional one.

    The second job is a real defect this rewrite removes: `argparse.REMAINDER` hands everything
    after the identifier to the child, so a trailing `--json` used to be passed to the child as an
    argument *and* silently leave AIROOT in human mode. Everything before the `--` separator belongs
    to AIROOT, so our own options are hoisted in front of the command where argparse reads them.
    """

    index = _command_token_index(arguments)
    if index is None or arguments[index] != "exec":
        return arguments

    head, tail = arguments[: index + 1], arguments[index + 1 :]
    if tail[:1] == [EXEC_ALIAS_FLAG] and len(tail) > 1:
        tail = [tail[1], *tail[2:]]
    elif tail[:1] and tail[0].startswith(f"{EXEC_ALIAS_FLAG}="):
        tail = [tail[0].split("=", 1)[1], *tail[1:]]

    if "--" not in tail:
        # No separator: the child command starts right after the identifier and REMAINDER owns it.
        return [*head, *tail]

    separator = tail.index("--")
    ours, child = tail[:separator], tail[separator:]
    hoisted: list[str] = []
    owned: list[str] = []
    position = 0
    while position < len(ours):
        token = ours[position]
        if token == "--root" and position + 1 < len(ours):
            hoisted += [token, ours[position + 1]]
            position += 2
            continue
        if token.startswith("-"):
            hoisted.append(token)
            position += 1
            continue
        owned.append(token)
        position += 1
    return [*arguments[:index], *hoisted, "exec", *owned, *child]


def main(argv: list[str] | None = None) -> int:
    arguments = _normalize_exec_argv(list(argv) if argv is not None else sys.argv[1:])
    # Scanned up front: a usage error happens before argparse gives us a namespace.
    want_json = "--json" in arguments
    args: argparse.Namespace | None = None
    try:
        # Before argparse: a declared-absent verb is not a typo, and reporting it as one (which is
        # what `invalid choice` did until §101) tells the caller to re-read their spelling instead
        # of the plan (ADR-0027).
        absent = declared_absent_verb(arguments)
        if absent is not None:
            raise _declared_absent_error(*absent)
        parser = build_parser()
        args = parser.parse_args(arguments)
        # doctor must be able to describe a broken root, so it resolves the path
        # without asserting root identity first.
        context = Context(args.root, verify=args.command != "doctor")
        _document, code = dispatch(args, context)
        return code
    except AirootError as error:
        _report_error(error, as_json=want_json)
        return error.exit_code
    except KeyboardInterrupt:
        error = AirootError(
            "PENDING_TRANSACTION",
            "interrupted; the transaction journal holds the last durable state",
            evidence=["run 'airoot repair' to reconcile"],
        )
        _report_error(error, as_json=want_json)
        return EXIT_RECOVERY


def _printable_error(document: dict[str, Any]) -> dict[str, Any]:
    """The failure document, self-validated — without ever **masking** the failure (draft §102).

    Every other outward document is validated before it can replace anything, so a validation
    failure is reported as ``SELF_VALIDATION_FAILED``. That is backwards here: the document being
    validated *is* the report of a failure, and swapping the caller's reason code for the
    implementer's would hide the thing they asked about. So a shape defect is appended to
    ``evidence`` — visible to the caller, the human output and the suite — and ``reason_code`` stays
    the one the command actually produced.
    """

    try:
        validate_self("error-response", document)
    except AirootError as defect:
        evidence = document.get("evidence")
        lines = list(evidence) if isinstance(evidence, list) else []
        return dict(document, evidence=[*lines, f"self-validation failed: {defect.message}"])
    return document


def _report_error(error: AirootError, *, as_json: bool) -> None:
    document = _printable_error(error.to_envelope())
    if as_json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(f"error: {error.reason_code}: {error.message}", file=sys.stderr)
        for item in document["evidence"][:8]:
            print(f"  - {item}", file=sys.stderr)
