"""Real-machine acceptance run for draft §17.5 (steps 8-9) and §18.5 (steps 5-6).

Run it by hand on a machine that has data roots to look at:

    python cli/tests/real_machine_acceptance.py
    python cli/tests/real_machine_acceptance.py --online   # also acquires from a real upstream

Without ``--online`` nothing touches the network, and the online step reports itself as **not run**
rather than passing quietly. ``--online`` is the only part that reaches an upstream, so the pytest
suite stays hermetic.

It is **not** a pytest module (the suite must not depend on the host's real `D:\\env`).
It never writes HKCU: the persisted path stops at the no-token / dry-run boundaries.

The scratch root lives in the system temp directory and is deleted on the way out.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))

from airoot.cli import main  # noqa: E402
from airoot.registry import Registry  # noqa: E402
from airoot.root import init_root  # noqa: E402

DATA_ROOT = Path(r"D:\env")
OBJECT = DATA_ROOT / "java"

#: Opt-in. See the draft §59 block at the end of `main_run` for why the network is not the default.
ONLINE = "--online" in sys.argv

#: The version asked of `rust-toolchain`. Its artifact URL is not version-templated (`rustup-init.exe`
#: is the rolling installer), so this only labels the resolution; the digest is what identifies it.
RUST_VERSION = "1.83.0"

ROOT = Path(tempfile.mkdtemp(prefix="airoot-acceptance-")) / "root"


def run(*argv: str) -> tuple[int, dict]:
    """Call the CLI in-process and capture its JSON (avoids a second interpreter)."""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(["--json", "--root", str(ROOT), *argv])
    text = buffer.getvalue().strip()
    return code, (json.loads(text) if text.startswith("{") else {"raw": text})


def show(label: str, code: int, document: dict, keys: tuple[str, ...] = ()) -> None:
    picked = {key: document.get(key) for key in keys}
    print(f"{label:<46} exit={code} {json.dumps(picked, ensure_ascii=True)}")


def main_run() -> int:
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
    # It stops **before** stage/commit on purpose. Those live behind the transaction's approval
    # token, and P1 has no production issuer — the only issuer is `cli/tests/fake_issuer.py`. So a
    # real install cannot be *approved* on this machine, and inventing a token here to make the run
    # look complete would be exactly the fake this project refuses. Download and verification are
    # separate units that need no approval, and those are what this step proves.
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
                "    boundary: stage/commit need an approval token, and P1 has no production "
                "issuer (only cli/tests/fake_issuer.py) - reported, not faked "
                "(ADR-0025 keeps it waiting for the P2 broker)"
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


if __name__ == "__main__":
    if not DATA_ROOT.is_dir():
        raise SystemExit(f"this check needs a real data root to look at: {DATA_ROOT} is missing")
    raise SystemExit(main_run())
