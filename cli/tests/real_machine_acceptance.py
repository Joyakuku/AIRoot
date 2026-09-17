"""Real-machine acceptance run for draft §17.5 (steps 8-9) and §18.5 (steps 5-6).

Run it by hand on a machine that has data roots to look at:

    python cli/tests/real_machine_acceptance.py
    python cli/tests/real_machine_acceptance.py --online   # also acquires from a real upstream

Without ``--online`` nothing touches the network, and the online step reports itself as **not run**
rather than passing quietly. ``--online`` is the only part that reaches an upstream, so the pytest
suite stays hermetic.

It is **not** a pytest module (the suite must not depend on the host's real `D:\\env`).
It never writes HKCU: the persisted path stops at the no-token / dry-run boundaries.

**And it now proves it.** The two halves are bracketed by host snapshots -- the machine and user
environment blocks, a per-file manifest of the AIROOT root and the two rustup trees, a summary of the
data root, the top level of the home directory and of this checkout, and the scratch directories this
run could have left behind -- and the pass fails if any of them moved. See the isolation contract at
the end of the file for what that does and does not cover.

The scratch root lives in the system temp directory and is deleted on the way out.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import winreg
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

from airoot.cli import main  # noqa: E402
from airoot.caps.identity import probe_identity  # noqa: E402
from airoot.registry import Registry  # noqa: E402
from airoot.root import init_root  # noqa: E402

DATA_ROOT = Path(r"D:\env")
OBJECT = DATA_ROOT / "java"

#: Opt-in. See the draft §59 block at the end of `main_run` for why the network is not the default.
ONLINE = "--online" in sys.argv

#: The version asked of `rust-toolchain`. Its artifact URL is not version-templated (`rustup-init.exe`
#: is the rolling installer), so this only labels the resolution; the digest is what identifies it.
RUST_VERSION = "1.83.0"

#: Created **lazily** by `main_run`, not at import. It used to be a module-level `mkdtemp`, which meant
#: every exit that never reached `main_run`'s cleanup — the "no data root here" refusal, an exception
#: before the registry block — left an empty `airoot-acceptance-*` directory behind. Measured: three of
#: them were sitting in the temp directory, alongside 39 more from earlier one-off development scripts
#: (draft §134). The audit below reports leftovers; this is the half that stops making them.
ROOT: Path | None = None


def run(*argv: str) -> tuple[int, dict]:
    """Call the CLI in-process and capture its JSON (avoids a second interpreter)."""
    import contextlib
    import io

    assert ROOT is not None, "main_run() has not been called, so there is no root to run against"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["--json", "--root", str(ROOT), *argv])
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text.startswith("{") else {"raw": text})


def show(label: str, code: int, document: dict, keys: tuple[str, ...] = ()) -> None:
    picked = {key: document.get(key) for key in keys}
    print(f"{label:<46} exit={code} {json.dumps(picked, ensure_ascii=True)}")


def main_run() -> int:
    global ROOT
    ROOT = Path(tempfile.mkdtemp(prefix="airoot-acceptance-")) / "root"
    shutil.rmtree(ROOT, ignore_errors=True)
    init_root(ROOT, root_instance_id="root-verify-step89", machine_id="host-verify-step89")
    registry = Registry.initialize(
        ROOT, machine_id="host-verify-step89", root_instance_id="root-verify-step89"
    )
    registry.close()

    failures = 0

    def check(label: str, condition: bool) -> None:
        nonlocal failures
        if not condition:
            failures += 1
            print(f"  !! FAILED: {label}")

    # Step 8: routing decisions ------------------------------------------------
    project = ROOT / "fake-project"
    project.mkdir(parents=True, exist_ok=True)
    (project / "requirements.txt").write_text("python==3.11.11\n", encoding="utf-8")

    code, doc = run("scope", "decide", "python", "--project", str(project))
    show("scope decide python --project <manifest>", code, doc, ("scope", "confirmation_required", "origin", "reason_code"))
    check("project-manifest reference must not ask", doc.get("confirmation_required") is False)
    check("project-manifest reference is project-isolated", doc.get("scope") == "project")

    code, doc = run("scope", "decide", "archive")
    show("scope decide archive (generic tool)", code, doc, ("scope", "confirmation_required", "origin"))
    check("the obvious single-file-tool case must not ask", doc.get("confirmation_required") is False)

    code, doc = run("scope", "decide", "java")
    show("scope decide java (no project)", code, doc, ("scope", "confirmation_required", "origin", "reason_code"))
    check("installing a runtime must ask", doc.get("confirmation_required") is True)
    check("...and it exits 4", code == 4)

    code, doc = run("scope", "decide", "java", "--creates-environment")
    show("scope decide java --creates-environment", code, doc, ("scope", "confirmation_required", "origin"))
    check("environment creation must ask", doc.get("confirmation_required") is True)

    code, doc = run("scope", "decide", "java", "--size-bytes", str(400 * 1024 * 1024))
    show("scope decide java --size-bytes 400MB", code, doc, ("scope", "confirmation_required", "size_estimate_bytes"))
    check("over-threshold must ask", doc.get("confirmation_required") is True)

    code, doc = run("scope", "decide", "totally-unknown-capability")
    show("scope decide <unknown>", code, doc, ("scope", "reason_code"))
    check("unknown capability is not routable", doc.get("reason_code") == "CAPABILITY_NOT_DECLARED")

    # Steward domain: real D:\env ---------------------------------------------
    code, doc = run("data-root", "add", str(DATA_ROOT), "--role", "runtime", "--id", "dr-env")
    show("data-root add " + str(DATA_ROOT), code, doc, ("files_touched",))
    check("adding a data root touches no file", doc.get("files_touched") == 0)

    code, doc = run("discover")
    reports = doc.get("reports") or []
    show("discover", code, doc, ("whitelist_revision", "files_touched"))
    print(f"    counts={reports[0].get('counts') if reports else None} revision={doc.get('whitelist_revision')}")
    # §141: the ledger has to describe *this* machine, so the capabilities this machine really
    # has under D:\env are named and asserted, not merely counted. `ffmpeg` is the one that used
    # to come out `unmanaged` purely because no capability named it.
    recognised = {
        str(obj.get("capability_id"))
        for report in reports
        for obj in (report.get("candidates") or [])
        if obj.get("management") == "external_reference"
    }
    print(f"    recognised={sorted(recognised)}")
    check(
        "every capability this machine actually has under D:\\env must be recognised",
        {"build", "java", "node", "ffmpeg"} <= recognised,
    )

    code, doc = run("adopt", str(OBJECT), "--mode", "reference")
    show("adopt D:\\env\\java --mode reference", code, doc, ("ownership", "files_touched"))
    reference = doc.get("reference") or {}
    external_id = reference.get("external_id")
    print(f"    external_id={external_id} version={reference.get('version')} entrypoints={reference.get('entrypoints')}")
    check(
        "the recorded entrypoint must keep its directory",
        any("/" in str(item) or "\\" in str(item) for item in (reference.get("entrypoints") or [])),
    )
    entry = Path(str(reference.get("path"))) / str((reference.get("entrypoints") or ["java.exe"])[0])
    check("the recorded entrypoint must exist", entry.is_file())

    # Step 9: session activation (no writes anywhere) --------------------------
    code, doc = run("env", "activate", external_id, "--shell", "powershell")
    script = doc.get("script", "")
    show("env activate (powershell)", code, doc, ("scope", "persisted", "variables"))
    check("session activation must not persist", doc.get("persisted") is False)
    check("script must set JAVA_HOME inside the data root", "JAVA_HOME" in script and str(DATA_ROOT) in script)
    print("    script -> " + script.strip().replace("\n", " | "))

    code, doc = run("exec", external_id, "cmd", "/c", "echo", "%JAVA_HOME%")
    show("exec external/dr-env/java -- cmd /c echo", code, doc, ("exit_status", "persisted", "variables"))
    check("exec must not persist", doc.get("persisted") is False)

    # §15.1 spells it `exec --env <name> -- <command>`; both spellings must be the same command,
    # and a `--json` written after the identifier belongs to AIROOT, not to the child.
    code, doc = run("exec", "--env", external_id, "--", "cmd", "/c", "echo", "%JAVA_HOME%")
    show("exec --env <id> -- cmd /c echo (§15.1)", code, doc, ("exit_status", "variables"))
    check("the planning spelling must run the child", code == 0 and doc.get("exit_status") == 0)

    code, doc = run("exec", external_id, "--json", "--", "cmd", "/c", "echo", "%JAVA_HOME%")
    check("a trailing --json must not reach the child", "--json" not in (doc.get("command") or []))

    # Plan / approval boundary -------------------------------------------------
    code, doc = run("env", "persist", external_id, "--dry-run")
    show("env persist --dry-run", code, doc, ("dry_run", "plan_hash"))
    check("dry run must not write", doc.get("dry_run") is True)

    code, doc = run("env", "persist", external_id)
    show("env persist (no token)", code, doc, ("reason_code", "required_action"))
    check("no token -> PERSISTENCE_REQUIRES_APPROVAL(4)", code == 4)
    plan_file = Path(doc.get("plan_file", "missing"))
    check("the plan must be on disk for a human to approve", plan_file.is_file())
    print(f"    plan_file={plan_file} hash={doc.get('plan_hash')}")

    code, doc = run("env", "persist", external_id, "--scope", "machine")
    show("env persist --scope machine", code, doc, ("reason_code",))
    check("machine scope needs the broker", code == 5)

    code, doc = run("env", "list", "--all")
    show("env list --all", code, doc, ("environment_persist",))
    check("nothing must have been persisted", doc.get("environment_persist") == [])

    code, doc = run("env", "forget", external_id, "--dry-run")
    show("env forget --dry-run (nothing recorded)", code, doc, ("reason_code",))
    check("forget with no record -> ENVIRONMENT_PERSIST_NOT_FOUND(8)", code == 8)

    # Step 5: steward-first where ---------------------------------------------
    code, doc = run("where", "java")
    show("where java (steward-first)", code, doc, ("found", "management", "selection_reason"))
    check("a healthy reference is selected without any flag", doc.get("found") is True)
    check("...and it is the reference", doc.get("management") == "external_reference")
    check(
        "selection_reason names the steward rule",
        doc.get("selection_reason") == "STEWARD_REFERENCE_HEALTHY",
    )
    check(
        "the policy is stated in the evidence",
        any("precedence=steward" in item["detail"] for item in doc["evidence"]),
    )
    check("no conflict code on the steward path", doc.get("reason_code") != "CONFLICT_MANAGED_BROKEN")
    print(f"    executable={doc.get('executable')}")

    code, doc = run("where", "java", "--version", ">=99")
    show("where java --version >=99", code, doc, ("found", "reason_code", "selection_reason"))
    check("an unsatisfiable constraint finds nothing", doc.get("found") is False)
    check("...and says the version is unsatisfied", doc.get("reason_code") == "VERSION_UNSATISFIED")

    # Step 6: doctor in the steward domain ------------------------------------
    code, doc = run("doctor")
    severities = {item["code"]: item["severity"] for item in doc.get("diagnostics", [])}
    show("doctor", code, doc, ("status",))
    print(f"    codes={json.dumps(severities, sort_keys=True, ensure_ascii=True)}")
    errors = [
        item["code"]
        for item in doc.get("diagnostics", [])
        if item["severity"] in {"error", "critical"}
    ]
    check("doctor finds no error-level diagnostic on the real data root", errors == [])
    check("data roots are actually checked", "DATA_ROOT_MISSING" not in severities)
    check("unmanaged noise stays out of the default run", "UNMANAGED_OBJECT_PRESENT" not in severities)

    code, doc = run("doctor", "--include-unmanaged")
    loud = sorted({item["code"] for item in doc.get("diagnostics", [])})
    show("doctor --include-unmanaged (before discover --record)", code, doc, ("status",))
    check("nothing is claimed about objects that were never recorded", "UNMANAGED_OBJECT_PRESENT" not in loud)

    code, doc = run("discover", "--record")
    show("discover --record", code, doc, ("recorded",))
    check("unmanaged observations get recorded", (doc.get("recorded") or 0) > 0)

    code, doc = run("doctor", "--include-unmanaged")
    loud = sorted({item["code"] for item in doc.get("diagnostics", [])})
    show("doctor --include-unmanaged (after recording)", code, doc, ("status",))
    print(f"    codes={json.dumps(loud, ensure_ascii=True)}")
    check("--include-unmanaged surfaces the recorded unmanaged objects", "UNMANAGED_OBJECT_PRESENT" in loud)
    check(
        "the default run still stays quiet",
        "UNMANAGED_OBJECT_PRESENT" not in {item["code"] for item in run("doctor")[1]["diagnostics"]},
    )

    code, doc = run("doctor", "--verify")
    print(f"    --verify codes={json.dumps(sorted({item['code'] for item in doc.get('diagnostics', [])}), ensure_ascii=True)}")
    for item in doc.get("diagnostics", []):
        if item["code"] == "REFERENCE_DRIFTED":
            print(f"    drift: {' | '.join(item['evidence'])}")

    # Step 11-12: deletion is graded by ownership; the boundary is machine-checkable ----
    code, doc = run("capability", "list")
    show("capability list", code, doc, ("revision", "count"))
    check("the frozen capability list is readable", (doc.get("count") or 0) >= 6)

    code, doc = run("capability", "check", str(OBJECT))
    show("capability check <real object>", code, doc, ("verdict", "capability_id", "managed_files_touched"))
    check("a real JDK directory is admitted", doc.get("verdict") == "adoptable")
    check("checking touches nothing", doc.get("managed_files_touched") == 0)

    code, doc = run("uninstall", external_id)
    show("uninstall <reference>", code, doc, ("reason_code",))
    check("a reference is never uninstallable", code == 7)
    check("...and the refusal names the legal command", any("airoot forget" in item for item in doc.get("evidence", [])))
    check("the real object is still there", OBJECT.is_dir())

    # Routing + plan in one artifact (draft §12.4) ------------------------------
    code, doc = run("plan", "java", "--scope", "data-root", "--target", "data-root:dr-env", "--dry-run")
    show("plan java --dry-run (routing + plan)", code, doc, ("confirmation_required", "required_approval", "size_estimate_bytes"))
    check("routing is reported", (doc.get("routing") or {}).get("decided_scope") is not None)
    check("an unknown size is reported as unknown", doc.get("size_note") == "SIZE_ESTIMATE_UNAVAILABLE" or doc.get("size_estimate_bytes"))

    code, doc = run("plan", "java", "--scope", "data-root", "--target", "data-root:dr-env", "--creates-environment")
    show("plan java --creates-environment", code, doc, ("reason_code",))
    check("a high-risk class must be confirmed before a plan exists", code == 4)
    check("...and no plan file is produced", doc.get("plan_file") is None)

    # Derived state can be rebuilt; authority cannot (draft §24) ------------------
    code, doc = run("rebuild", "--plan")
    show("rebuild --plan", code, doc, ("operation",))
    check("a plain rebuild plan writes nothing", not (ROOT / "state" / "rebuild").exists())

    code, doc = run("rebuild")
    show("rebuild", code, doc, ("database_touched", "files_deleted", "adopted"))
    check("rebuild never touches the authority", doc.get("database_touched") is False)
    check("rebuild deletes nothing", doc.get("files_deleted") == 0)
    check("rebuild adopts nothing", doc.get("adopted") == 0)
    check("the previous projections were archived", Path(str(doc.get("archived_to"))).is_dir())

    # Read-only observation surface (draft §26) --------------------------------
    code, doc = run("path", "verify")
    show("path verify (real machine PATH)", code, doc, ("violations", "path_written", "launcher_present"))
    check("PATH is read-only for this verb", doc.get("path_written") is False)
    check("no AIROOT entry is on the host PATH", doc.get("violations") == 0)

    code, doc = run("tool", "list")
    show("tool list (own nothing yet)", code, doc, ("count",))
    check("references are not owned instances", doc.get("count") == 0)

    # Desired layer: intent, not declared state (draft §27) ---------------------
    code, doc = run("tool", "pin", "java", "--version", "25.0.2.0")
    show("tool pin java (no trusted source)", code, doc, ("manifest_revision", "in_sync", "active_binding_changed"))
    check("pinning records intent", doc.get("manifest_revision") == 1)
    check("pinning never changes the binding", doc.get("active_binding_changed") is False)
    check("a missing source is reported, not invented", doc.get("sync", [{}])[0].get("plan_blocked_by") == "NOT_FOUND")

    code, doc = run("tool", "pin", "java", "--clear")
    show("tool pin --clear", code, doc, ("desired",))
    check("the pin can be removed", doc.get("desired") == [])

    # Posture, in the run's own words (minimum version, judgement 16) ------------------
    #
    # Three of that judgement's clauses are measurable here; the fourth is not, and saying which is
    # which is the point:
    #
    #   * `policy_only` + `same_user_can_bypass` -- asserted below from the root's own document;
    #   * "no machine PATH write" -- `path verify` says `path_written is False` (checked above) and the
    #     isolation audit compares the machine environment block before and after;
    #   * "no elevation" -- **not asserted, because it would measure the operator's shell.** Measured:
    #     this session's token is elevated (`integrity=high`, SID `…-500`), so an assertion here would
    #     fail on this machine for a reason that has nothing to do with the product. What is asserted
    #     instead is the clause that holds under *any* token: the write that would need elevation is
    #     refused even when the token has it (checked above, `--scope machine` -> `PRIVILEGE_REQUIRED`).
    token = probe_identity()
    print(f"    token: integrity={token.integrity} elevated={token.elevated} sid={token.sid}")
    code, status = run("root", "status")
    show("root status", code, status, ("security_mode", "enforcement", "registry_generation"))
    check("the root declares policy_only", status.get("security_mode") == "policy_only")
    check(
        "and the enforcement word that goes with it",
        status.get("enforcement") == "same_user_can_bypass",
    )

    code, inventory = run("inventory", "--class", "external_reference")
    references = inventory.get("external_references") or []
    show("inventory --class external_reference", code, inventory, ("reason_code",))
    check("the adopted reference is in the inventory", code == 0 and len(references) == 1)
    check("...and the data root is listed beside it", bool(inventory.get("data_roots")))

    # Step 31: the search protocol surface over a bounded crawl (read-only) -----
    code, doc = run("search", "java", "--ext", ".exe")
    data = doc.get("data") or {}
    show("search java --ext .exe", code, doc, ("status", "reason_code"))
    print(
        f"    roots={len(doc.get('evidence', []))} matched={data.get('stats', {}).get('matched')} "
        f"returned={data.get('stats', {}).get('returned')} freshness={(data.get('freshness') or {}).get('state')}"
    )
    # The four-way confession is the whole point: a crawl answer must never look like an index
    # answer (ADR-0017). A timeout is also acceptable here — it says so too.
    if doc.get("status") == "timed_out":
        check("a timeout reports SEARCH_TIMEOUT", doc.get("reason_code") == "SEARCH_TIMEOUT")
    else:
        check("a crawl answer is degraded", doc.get("status") == "degraded")
        check("...and says it used the fallback", doc.get("reason_code") == "SEARCH_FALLBACK_USED")
        check("...with data.fallback.kind=crawl", (data.get("fallback") or {}).get("kind") == "crawl")
        check("...and freshness unknown/none", (data.get("freshness") or {}).get("state") == "unknown")
    check("no index was written", not (ROOT / "cache" / "search").exists())

    code, doc = run("search", "status")
    show("search status", code, doc, ("operation", "reason_code", "implementation_id"))
    check("status reports no index rather than a stale one", doc.get("freshness", {}).get("state") == "unknown")
    check("status names the crawl implementation", doc.get("implementation_id") == "airoot-native-search-crawl")

    code, doc = run("search", "--query", "java", "--limit", "1")
    data = doc.get("data") or {}
    cursor = data.get("next_cursor")
    print(f"    query via --query: matched={data.get('stats', {}).get('matched')} cursor={'yes' if cursor else 'no'}")
    if cursor:
        code, second = run("search", "--query", "java", "--limit", "1", "--cursor", cursor)
        show("search --query java --cursor <next>", code, second, ("reason_code",))
        check("a cursor is accepted for the same query", code == 2)
        code, foreign = run("search", "--query", "python", "--cursor", cursor)
        show("same cursor, different query", code, foreign, ("reason_code",))
        check("a cursor from another query is refused", foreign.get("reason_code") == "SEARCH_CURSOR_INVALID")

    # Step 32: the crawl-built index (still no USN journal; the index is derived state) ----
    code, doc = run("search", "refresh")
    show("search refresh", code, doc, ("records", "coverage", "data_root_files_touched"))
    print(f"    built_at={doc.get('built_at')} index={doc.get('index_path')}")
    check("refresh builds an index over the data roots", int(doc.get("records") or 0) > 0)
    check("refresh replaces one derived file and touches no data-root file", doc.get("data_root_files_touched") == 0)
    index_file = Path(str(doc.get("index_path", "missing")))
    check("the index file exists", index_file.is_file())
    check(
        "no -wal/-shm/*.tmp leftovers",
        sorted(item.name for item in index_file.parent.iterdir()) == [index_file.name],
    )

    code, doc = run("search", "java", "--ext", ".exe")
    data = doc.get("data") or {}
    show("search java --ext .exe (index)", code, doc, ("status", "reason_code"))
    print(f"    freshness={data.get('freshness')} fallback={data.get('fallback')}")
    check("an index answer is a healthy answer", code == 0 and doc.get("status") == "ok")
    check("...with real freshness", (data.get("freshness") or {}).get("state") == "current")
    check("...and no crawl fallback", data.get("fallback") is None)
    check(
        "...and results marked as indexed",
        {item.get("verification") for item in data.get("results", [])} in ({"indexed"}, set()),
    )

    code, doc = run("search", "status")
    show("search status (after refresh)", code, doc, ("operation", "reason_code"))
    check("status is healthy once an index exists", code == 0 and doc.get("reason_code") == "SUCCESS")
    check("status reports the record count", int((doc.get("index") or {}).get("records") or 0) > 0)

    # Step 33: the search index shows up in the diagnosis surface ---------------------------
    code, doc = run("doctor")
    codes_before = doc.get("codes")
    show("doctor (before any index)", code, doc, ("status",))
    print(f"    codes={codes_before}")
    check(
        "doctor does not nag about a search index that was never built",
        not any(str(item).startswith("SEARCH_") for item in (codes_before or [])),
    )

    code, doc = run("search", "refresh")
    show("search refresh (for the doctor check)", code, doc, ("records", "coverage"))
    code, doc = run("doctor")
    codes_after = doc.get("codes")
    show("doctor (fresh index)", code, doc, ("status",))
    print(f"    codes={codes_after}")
    check(
        "a fresh index produces no new diagnostic either",
        not any(str(item).startswith("SEARCH_") for item in (codes_after or [])),
    )

    # Step 34: why is there no native (USN) index? Ask, read-only, explicitly ------------------
    code, doc = run("search", "implementations")
    native = [item for item in doc.get("implementations", []) if item.get("implementation_kind") == "native"]
    show("search implementations", code, doc, ("revision",))
    check("the native candidate is named even though it is unavailable", len(native) == 1)
    check("...and says why", native and native[0].get("reason_code") == "SEARCH_BACKEND_UNAVAILABLE")

    code, doc = run("search", "status", "--probe-native-index")
    native = doc.get("native_index") or {}
    probe = native.get("probe") or {}
    show("search status --probe-native-index", code, doc, ("reason_code",))
    print(f"    filesystem={probe.get('filesystem')} elevated={probe.get('elevated')}")
    print(f"    journal_present={probe.get('journal_present')} enumeration_bytes={probe.get('enumeration_bytes')}")
    print(f"    unprivileged_read={probe.get('unprivileged_read')}")
    for item in probe.get("observations", [])[:4]:
        print(f"    observation: {item}")
    check("the probe is marked as actually run", native.get("checked") is True)
    check("the probe reports raw observations, not just a verdict", bool(probe.get("observations")))
    check(
        "whatever the machine says, the build still reports the native index as unavailable",
        native.get("available") is not True or probe.get("native_index_available") is True,
    )

    # ---- draft §59: the real acquisition path, when asked for online ----------
    #
    # Everything above runs offline and hermetic. This block is the one place that talks to the
    # network, so it is **opt-in**: the pytest suite must never depend on an upstream being up.
    # When it is not requested it says so rather than passing quietly, because "not checked" and
    # "checked and fine" must not look alike (draft §50's lesson, applied to a real machine).
    #
    # It stops **before** stage/commit on purpose. Those live behind the transaction's approval token,
    # and since ADR-0046 that token comes from an **explicit local signing step** (`airoot.tx.issuer`:
    # provision this root's key, then issue) rather than from anything this script may do on the
    # operator's behalf. An acceptance run that provisioned a key and signed for you would erase exactly
    # the record an approval exists to be, so the boundary is reported rather than faked. (Draft §117 did
    # run the whole path on this machine once, deliberately: plan -> explicit issuance -> install
    # FINALIZED, 12 721 664 bytes, digest identical to the published checksum.) Download and verification
    # are separate units that need no approval, and those are what this step proves.
    if not ONLINE:
        print(f"{'online acquisition (draft 59)':<46} not run (pass --online)")
    else:
        from airoot.caps.backends.https_artifact import HttpsArtifactBackend
        from airoot.caps.sources import resolve_source

        resolved = resolve_source(capability_id="rust-toolchain", version=RUST_VERSION)
        source = resolved.to_document()
        show(
            "source resolve (online, real upstream)",
            0,
            source,
            ("artifact_url", "expected_digest", "backend_id"),
        )
        check("resolution is online, not against a local file", source.get("offline") is False)
        check(
            "the expected digest came from the published checksum file, not from the artifact",
            str(source.get("expected_digest", "")).startswith("sha256:"),
        )

        destination = ROOT.parent / "rustup-init.exe"
        backend = HttpsArtifactBackend()
        try:
            artifact = backend.fetch(locator=str(source["artifact_url"]), destination=destination)
            result = backend.verify(artifact, expected_digest=str(source["expected_digest"]))
            print(
                f"    fetched {artifact.size} bytes -> {artifact.path.name} "
                f"digest={artifact.digest[:23]}"
            )
            show(
                "https_artifact fetch + verify",
                0,
                {"verified": result.ok, "size": result.size, "problems": result.problems},
                ("verified", "size"),
            )
            check("the published digest verifies against the downloaded bytes", result.ok is True)
            check("what came back is a plausible executable, not an error page", artifact.size > 500_000)
            check(
                "the digest is computed from the bytes, so it cannot match itself",
                result.digest == artifact.digest,
            )
            print(
                "    boundary: stage/commit need an approval token, and signing one is an explicit "
                "local step (airoot.tx.issuer, ADR-0046) - not taken here, reported not faked"
            )
        except Exception as exc:  # noqa: BLE001 - an acceptance run reports, it does not explode
            check(f"online acquisition failed: {type(exc).__name__}: {exc}", False)

    # Leaving the registry clean, then removing the scratch root --------------
    registry = Registry.open(ROOT)
    try:
        code, doc = run("forget", external_id)
        show("forget reference", code, doc, ("files_touched", "source_unchanged"))
        check("forgetting a reference touches no file", doc.get("files_touched") == 0)
        check("the referenced object is unchanged", doc.get("source_unchanged") is True)
        java_exe = Path(str(reference.get("path"))) / str(
            (reference.get("entrypoints") or ["java.exe"])[0]
        )
        print(f"    {java_exe} still present: {java_exe.is_file()}")
        check("the referenced entrypoint is still there after forget", java_exe.is_file())
    finally:
        registry.close()
        shutil.rmtree(ROOT.parent, ignore_errors=True)

    print(f"\nstep 5-6 + 8-9 + 31-34 real-machine acceptance: {'PASS' if failures == 0 else 'FAIL'} ({failures} failed check(s))")
    return 1 if failures else 0


def closed_loop() -> int:
    """The whole minimum-version loop, on a scratch root, **without the network** (W6).

    `docs/AIROOT-最小版本-v1.md` defines "done" as one closed loop: plan -> issue -> approve ->
    install -> FINALIZED -> verify -> run -> where -> stable entry -> retire -> gc -> doctor, with the
    payload **really deleted** at the end. This runs it end to end with an `adopt --mode import` plan,
    so the artifact is a local file: no upstream, no token beyond the root's own signer, nothing written
    outside the scratch root.

    Two properties get their own checks because they are the ones that quietly rot:

    * **idempotence** -- the second `install` of the same plan must be terminal already and must not bump
      the registry generation again;
    * **the entry outliving the binding** -- after `retire`, `path verify` must report exactly the drift
      ADR-0050 named (a stable entry with no binding), not a clean bill of health.
    """

    import sys as _sys

    failures = 0

    def check(label: str, condition: bool) -> None:
        nonlocal failures
        print(f"  [{'ok ' if condition else 'FAIL'}] {label}")
        if not condition:
            failures += 1

    root = Path(tempfile.mkdtemp(prefix="airoot-closed-loop-")) / "root"
    payload_source = Path(tempfile.mkdtemp(prefix="airoot-closed-loop-payload-")) / "probe-tool.exe"
    shutil.rmtree(root, ignore_errors=True)
    init_root(root, root_instance_id="root-closed-loop", machine_id="host-closed-loop")
    Registry.initialize(
        root, machine_id="host-closed-loop", root_instance_id="root-closed-loop"
    ).close()
    shutil.copy2(_sys.executable, payload_source)

    def call(*argv: str) -> tuple[int, dict]:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--json", "--root", str(root), *argv])
        text = buffer.getvalue().strip()
        return code, (json.loads(text) if text.startswith("{") else {"raw": text})

    print("\nclosed loop (minimum version, scratch root, no network)")

    try:
        code, adopted = call(
            "adopt", str(payload_source), "--mode", "import", "--capability", "archive", "--version", "9.9.9"
        )
        plan_file = str(adopted.get("plan_file") or "")
        show("adopt --mode import", code, adopted, ("plan_file", "reason_code"))
        check("import produced a plan file", code == 0 and Path(plan_file).is_file())
        if not plan_file:
            return failures

        token = root / "state" / "approvals" / "loop-token.json"
        code, issued = call("issue", plan_file, "--out", str(token), "--provision")
        show("issue --provision", code, issued, ("provisioned", "permission_proof", "reason_code"))
        check("the verb signed a token", code == 0 and token.is_file())
        check("and says what it is worth", issued.get("permission_proof") is False)

        key = root / "state" / "issuer-key.json"
        before = key.read_bytes()
        again, repeated = call("issue", plan_file, "--out", str(token), "--provision")
        show("issue --provision (again)", again, repeated, ("reason_code",))
        check("a second --provision is refused, not a rotation", again == 8 and key.read_bytes() == before)

        code, approved = call("approve", plan_file, "--token-file", str(token))
        show("approve", code, approved, ("approval_id", "reason_code"))
        check("the approval is recorded", code == 0)

        code, installed = call("install", plan_file, "--token-file", str(token))
        show("install", code, installed, ("state", "instance_id", "generation_after", "reason_code"))
        instance = str(installed.get("instance_id") or "")
        generation = installed.get("generation_after")
        check("the real payload installs and FINALIZEs", code == 0 and installed.get("state") == "FINALIZED")

        second = root / "state" / "approvals" / "loop-token-2.json"
        call("issue", plan_file, "--out", str(second))
        code, replayed = call("install", plan_file, "--token-file", str(second))
        show("install (again)", code, replayed, ("state", "generation_after", "reason_code"))
        check(
            "installing the same plan again is terminal, not a second install",
            code == 0
            and replayed.get("state") == "FINALIZED"
            and replayed.get("generation_after") == generation,
        )

        code, verified = call("tool", "verify", instance)
        show("tool verify", code, verified, ("verified", "problems"))
        check("the payload verifies against its registered digest", verified.get("verified") is True)

        code, ran = call("run", "--capability", "archive", "--", "--version")
        show("run --capability", code, ran, ("exit_status", "persisted", "reason_code"))
        check("the payload really starts", ran.get("exit_status") == 0)
        check("and nothing was persisted by running it", ran.get("persisted") is False)

        entry = root / "cli" / "exposure" / "bin" / "archive.cmd"
        code, where = call("where", "archive")
        show("where archive", code, where, ("management", "launcher", "selection_reason"))
        # Compared **resolved**: the root comes from `mkdtemp` while the CLI canonicalizes it, so the
        # two spellings of the same file can differ (this check failed on exactly that once).
        named = where.get("launcher")
        check(
            f"where names the stable entry ({named!r})",
            bool(named) and Path(str(named)).resolve() == entry.resolve() and entry.is_file(),
        )
        check("and it is the version-independent one", entry.read_bytes().count(b"--") >= 1)

        code, path = call("path", "verify")
        show("path verify", code, path, ("launcher_present", "violations", "expected_launchers"))
        check("the stable entry is present", path.get("launcher_present") is True)
        check("and the PATH invariant holds", path.get("violations") == 0)
        check("the entry matches what this build writes", path.get("launchers", [{}])[0].get("matches_current") is True)

        # Judgement 11, on the real loop: switching the active version must not rewrite the entry and
        # must not touch the machine PATH. The entry's bytes are the whole promise (ADR-0050), and the
        # PATH is read here rather than assumed -- nothing in this script writes it, which is exactly
        # the kind of claim that should be measured rather than stated.
        from airoot.caps.effective import machine_path as _machine_path

        entry_before = entry.read_bytes()
        path_before = list(_machine_path())
        code, switch_plan = call(
            "adopt", str(payload_source), "--mode", "import", "--capability", "archive", "--version", "9.9.10"
        )
        switch_file = str(switch_plan.get("plan_file") or "")
        switch_token = root / "state" / "approvals" / "loop-switch-token.json"
        call("issue", switch_file, "--out", str(switch_token))
        call("approve", switch_file, "--token-file", str(switch_token))
        code, switched = call("install", switch_file, "--token-file", str(switch_token))
        show("install (new version)", code, switched, ("state", "instance_id", "generation_after"))
        new_instance = str(switched.get("instance_id") or "")
        code, where_after = call("where", "archive")
        show("where archive (after switch)", code, where_after, ("instance_id", "launcher", "version"))
        check(
            "the switch bound the new version",
            code == 0 and bool(new_instance) and new_instance != instance
            and where_after.get("instance_id") == new_instance,
        )
        check("and left the stable entry byte-identical", entry.read_bytes() == entry_before)
        check(
            "and named the same entry path",
            bool(where_after.get("launcher"))
            and Path(str(where_after["launcher"])).resolve() == entry.resolve(),
        )
        check("and did not touch the machine PATH", list(_machine_path()) == path_before)
        instance = new_instance  # the rest of the loop retires and collects what is bound now

        code, retired = call("tool", "retire", instance)
        show("tool retire", code, retired, ("payload_removed", "reason_code"))
        check("retiring clears the binding and keeps the payload", retired.get("payload_removed") is False)

        code, drifted = call("path", "verify")
        show("path verify (after retire)", code, drifted, ("launcher_present", "violations"))
        check(
            "an entry with no binding is reported as drift, not as healthy",
            code == 2 and drifted.get("violations") == 1,
        )

        code, gc_plan = call("tool", "gc", "--plan")
        show("tool gc --plan", code, gc_plan, ("collectable", "reason_code"))
        check("the retired payload is collectable", gc_plan.get("collectable") == 1)

        plan_entry = (gc_plan.get("plans") or [{}])[0]
        gc_file = str(plan_entry.get("plan_file") or "")
        if gc_file:
            gc_token = root / "state" / "approvals" / "loop-gc-token.json"
            call("issue", gc_file, "--out", str(gc_token))
            code, collected = call("tool", "gc", "--apply", "--token-file", str(gc_token))
            show("tool gc --apply", code, collected, ("payloads_removed", "reason_code"))
            store_dir = root / "store" / instance
            check("gc really deletes the payload", code == 0 and not store_dir.exists())
        else:
            check("gc --plan named a plan file to approve", False)

        code, doctor = call("doctor")
        errors = [
            item
            for item in doctor.get("diagnostics", [])
            if item.get("severity") in {"error", "warning"}
        ]
        show("doctor", code, doctor, ("status", "reason_code"))
        check("doctor reports no error after the loop", code == 0 and not errors)
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)
        shutil.rmtree(payload_source.parent, ignore_errors=True)

    print(f"closed loop: {'PASS' if failures == 0 else 'FAIL'} ({failures} failed check(s))")
    return failures


# --- the isolation contract -----------------------------------------------------------------------
#
# This script is the only part of the project that runs against the real machine, so "it does not touch
# your environment" cannot stay a sentence in the module docstring. It is **measured**: every surface
# AIROOT could reach is snapshotted before and after, and a single difference is a failure.
#
# What is covered, and how strongly:
#
#   * the machine and user environment blocks -- read with `winreg`, never written; equality is exact;
#   * `D:\env\.airoot`, `~\.cargo` and `~\.rustup` -- a per-file `(size, mtime_ns)` manifest, exact;
#   * `D:\env` itself -- `(files, bytes, newest mtime)`, because a per-file manifest of ~190 000 entries
#     taken twice is a lot of memory for a check whose answer is "no file was written". The summary
#     catches an added, a deleted or a resized file and any write that moves the newest mtime forward;
#     it does not catch a same-size edit with the timestamp put back, and nothing would;
#   * the system temp directory -- *leftovers only*: every scratch directory this script makes carries
#     `SCRATCH_PREFIX`, and one that survives the run is reported by name. "It cleans up after itself"
#     was a sentence in the docstring; this is the measurement of it;
#   * the **top level** of the home directory and of this checkout -- the watched trees above are a list
#     somebody wrote down, and this is the surface around it. A file appearing in either place is
#     reported by name; a file *edited* inside is not, which is the honest limit of a listing.
#
# The surfaces deliberately *not* here, named so their absence is a decision rather than an oversight:
# the process environment (a child cannot change its parent's), PATH (this script never writes it) and
# elevation (nothing in the pass asks for it).

WATCHED_TREES = (DATA_ROOT / ".airoot", Path.home() / ".cargo", Path.home() / ".rustup")

MACHINE_ENVIRONMENT = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

#: Two directories whose *top level* is compared, because the watched trees above are a list I wrote
#: down and this is the surface around them. A verb that dropped a file into the home directory or into
#: this checkout would have gone unnoticed by every other check here — the same shape §131 found when a
#: hand-written list of banned values met a value nobody had written down. Names, not mtimes: editing a
#: file inside does not change the listing, and this is about things *appearing*.
WATCHED_LISTINGS = (Path.home(), Path(__file__).resolve().parents[2])


def _listing(path: Path) -> list[str]:
    """Top-level entry names, so an appearing or disappearing file is visible without a full walk."""

    try:
        return sorted(item.name for item in path.iterdir())
    except OSError:
        return []

#: The prefix every scratch directory of this script uses, so "it cleans up after itself" is a
#: measurement rather than a sentence in the docstring: leftovers are reported by name.
#:
#: **Directories only**, and that is a measurement too: the first version of this check counted every
#: entry with the prefix and reported 48 where the directories were 42. The other six were files — this
#: session's own redirected logs and `airoot-rust-*.json` artifacts from an earlier one — which share
#: the prefix without being scratch roots. A check that can be moved by a log filename is measuring the
#: wrong thing.
SCRATCH_PREFIX = "airoot-"


def _scratch_entries() -> list[str]:
    """Scratch directories under the system temp directory that this pass could have created."""

    try:
        return sorted(
            item.name
            for item in Path(tempfile.gettempdir()).iterdir()
            if item.name.startswith(SCRATCH_PREFIX) and item.is_dir()
        )
    except OSError:
        return []


def _environment_block(hive: int, subkey: str) -> dict[str, str]:
    """One registry environment block, read-only. An unreadable key is data, not an exception."""

    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            count = winreg.QueryInfoKey(key)[1]
            values: dict[str, str] = {}
            for index in range(count):
                name, value, _ = winreg.EnumValue(key, index)
                values[name] = str(value)
            return values
    except OSError as exc:
        return {"<unreadable>": str(exc)}


def _file_manifest(path: Path) -> dict[str, tuple[int, int]]:
    """Per-file `(size, mtime_ns)`, keyed by relative posix path."""

    manifest: dict[str, tuple[int, int]] = {}
    if not path.is_dir():
        return manifest
    for base, directories, names in os.walk(path):
        directories.sort()
        for name in sorted(names):
            file = Path(base) / name
            try:
                info = file.stat()
            except OSError:
                continue
            manifest[file.relative_to(path).as_posix()] = (info.st_size, info.st_mtime_ns)
    return manifest


def _tree_summary(path: Path) -> tuple[int, int, int]:
    """`(files, bytes, newest mtime_ns)` for a tree too large to list twice."""

    files = 0
    total = 0
    newest = 0
    if not path.is_dir():
        return files, total, newest
    for base, _directories, names in os.walk(path):
        for name in names:
            try:
                info = (Path(base) / name).stat()
            except OSError:
                continue
            files += 1
            total += info.st_size
            newest = max(newest, info.st_mtime_ns)
    return files, total, newest


def host_state() -> dict:
    """Every surface the real-machine pass must leave exactly as it found it."""

    return {
        "machine environment": _environment_block(winreg.HKEY_LOCAL_MACHINE, MACHINE_ENVIRONMENT),
        "user environment": _environment_block(winreg.HKEY_CURRENT_USER, "Environment"),
        "trees": {str(path): _file_manifest(path) for path in WATCHED_TREES},
        "listings": {str(path): _listing(path) for path in WATCHED_LISTINGS},
        "data root": {"path": str(DATA_ROOT), "summary": _tree_summary(DATA_ROOT)},
        "scratch entries": _scratch_entries(),
    }


def isolation_problems(before: dict, after: dict) -> list[str]:
    """Every difference between two host snapshots, as a report rather than an assertion."""

    problems: list[str] = []
    for block in ("machine environment", "user environment"):
        old, new = before[block], after[block]
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                problems.append(f"{block}: {name} changed")
    for tree, old in before["trees"].items():
        new = after["trees"].get(tree, {})
        appeared = sorted(set(new) - set(old))
        disappeared = sorted(set(old) - set(new))
        changed = sorted(name for name in set(old) & set(new) if old[name] != new[name])
        if appeared:
            problems.append(f"{tree}: {len(appeared)} file(s) appeared, e.g. {appeared[:3]}")
        if disappeared:
            problems.append(f"{tree}: {len(disappeared)} file(s) disappeared, e.g. {disappeared[:3]}")
        if changed:
            problems.append(f"{tree}: {len(changed)} file(s) changed, e.g. {changed[:3]}")
    if before["data root"] != after["data root"]:
        problems.append(
            f"{before['data root']['path']}: summary moved {before['data root']['summary']} "
            f"-> {after['data root']['summary']}"
        )
    # Leftovers only. An entry that was already there when the pass started is not the pass's doing —
    # a crashed earlier run can leave one — so the question is "did this run add any?".
    left = sorted(set(after["scratch entries"]) - set(before["scratch entries"]))
    if left:
        problems.append(f"the pass left {len(left)} scratch entr(y/ies) behind: {left}")
    for directory, old in before["listings"].items():
        new = after["listings"].get(directory, [])
        appeared = sorted(set(new) - set(old))
        disappeared = sorted(set(old) - set(new))
        if appeared:
            problems.append(f"{directory}: {len(appeared)} entry/entries appeared, e.g. {appeared[:5]}")
        if disappeared:
            problems.append(f"{directory}: {len(disappeared)} entry/entries disappeared, e.g. {disappeared[:5]}")
    return problems


def isolation_report(before: dict, after: dict) -> int:
    """Compare the two snapshots, and prove the comparison is not vacuous on this machine's own data.

    The run is *supposed* to find nothing, so a comparison that can never find anything would look
    exactly like a clean machine. Five synthetic snapshots derived from `before` close that: a file
    appearing, an environment value changing, the data-root summary moving, a scratch leftover and an
    appearing entry in a watched listing must each be reported.
    """

    problems = isolation_problems(before, after)

    planted = copy.deepcopy(before)
    tree = next(iter(planted["trees"]))
    planted["trees"][tree]["planted-by-the-self-check.txt"] = (1, 1)
    moved = copy.deepcopy(before)
    block = moved["machine environment"] or moved["user environment"]
    if block:
        moved["machine environment"][next(iter(block))] = "<changed by the self-check>"
    grown = copy.deepcopy(before)
    files, total, newest = grown["data root"]["summary"]
    grown["data root"]["summary"] = (files + 1, total, newest)
    littered = copy.deepcopy(before)
    littered["scratch entries"] = sorted(before["scratch entries"] + [f"{SCRATCH_PREFIX}planted-by-the-self-check"])
    dropped = copy.deepcopy(before)
    dropped_directory = next(iter(dropped["listings"]))
    dropped["listings"][dropped_directory].append("planted-by-the-self-check.txt")

    self_check = []
    for label, snapshot in (
        ("a planted file", planted),
        ("a changed value", moved),
        ("a moved summary", grown),
        ("a scratch leftover", littered),
        ("an appearing entry in a watched listing", dropped),
    ):
        if not isolation_problems(before, snapshot):
            self_check.append(label)

    environment_values = len(before["machine environment"]) + len(before["user environment"])
    watched = sum(len(manifest) for manifest in before["trees"].values())
    listed = sum(len(names) for names in before["listings"].values())
    print("\nisolation audit (nothing outside a scratch root may change)")
    print(
        f"  compared: {environment_values} environment value(s), {watched} file(s) in "
        f"{len(before['trees'])} watched tree(s), a {before['data root']['summary'][0]}-file "
        f"summary of {before['data root']['path']}, {listed} top-level entr(y/ies) in "
        f"{len(before['listings'])} watched listing(s), and {len(before['scratch entries'])} scratch "
        "entr(y/ies) under the system temp directory"
    )
    for problem in problems:
        print(f"  [FAIL] {problem}")
    for label in self_check:
        print(f"  [FAIL] the comparison does not report {label}")
    # Pre-existing debris is reported, never failed on: it is not this run's doing, and a check that
    # fails on history is a check people learn to ignore. It is still worth seeing, because it can only
    # grow — 42 entries from earlier one-off session scripts were sitting there when this line was
    # written (draft §134), and the operator decides whether they go.
    if before["scratch entries"]:
        oldest = before["scratch entries"][0]
        print(
            f"  [note] {len(before['scratch entries'])} scratch entr(y/ies) were already there, e.g. "
            f"{oldest}; not created by this pass, and not removed by it either"
        )
    if not problems and not self_check:
        print("  [ok ] no tracked surface moved, and the comparison reports a planted change")
    print(f"isolation: {'PASS' if not problems and not self_check else 'FAIL'} "
          f"({len(problems)} difference(s))")
    return len(problems) + len(self_check)


if __name__ == "__main__":
    if not DATA_ROOT.is_dir():
        raise SystemExit(f"this check needs a real data root to look at: {DATA_ROOT} is missing")
    # Both halves always run: an early failure must not hide whether the closed loop still works.
    # The snapshots bracket them, so the pass is inert on the host or it says so. The `finally` is the
    # second half of "it cleans up after itself": `main_run` removes its own scratch root on the way
    # out, but only if it gets that far, and an exception in between used to leave the directory (and
    # the empty one created at import) behind.
    try:
        before = host_state()
        failures = main_run() | closed_loop() | isolation_report(before, host_state())
    finally:
        if ROOT is not None:
            shutil.rmtree(ROOT.parent, ignore_errors=True)
    raise SystemExit(failures)
