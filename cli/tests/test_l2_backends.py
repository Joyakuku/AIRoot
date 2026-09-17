"""L2: the install backend layer (三大核心契约 §4.4, draft §21).

What these tests defend:

* the nine frozen declarations exist, and a backend that would mutate the source or execute
  scripts cannot even be constructed;
* a local script-free artifact installs into the immutable store through the *same* state
  machine as the fixture, with the source left untouched;
* a tampered artifact is refused before anything reaches the store;
* https only — plaintext and downgrading redirects are refused, and the digest is mandatory.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

import fake_issuer
from airoot.caps.backends import (
    BACKEND_IDS,
    Artifact,
    BACKEND_OPERATIONS,
    DECLARED_FIELDS,
    BackendDeclaration,
    assert_script_free,
    declaration_for,
    resolve_backend,
    sha256_file,
)
from airoot.caps.backends.https_artifact import HttpsArtifactBackend
from airoot.caps.backends.portable_file import PortableFileBackend
from airoot.clock import FakeClock
from airoot.exits import AirootError
from airoot.tx.artifact import ArtifactRunner, create_artifact_plan


@pytest.fixture
def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"AIROOT-REAL-ARTIFACT-V1\nscript-free payload\n")
    return path


def install(registry, clock, root, source: Path, *, digest: str | None = None, version: str = "2.0.0"):
    backend = resolve_backend("portable_file")
    plan = create_artifact_plan(
        registry,
        backend,
        capability_id="fake-tool",
        version=version,
        kind="managed_tool",
        locator=str(source),
        source_digest=digest or sha256_file(source),
        clock=clock,
        plan_id=f"plan/real/{version}",
    )
    fake_issuer.install_keyring(root.path)
    token = fake_issuer.issue(plan, clock=clock)
    return ArtifactRunner(registry, backend, clock=clock).commit(plan, token), plan, backend


# --------------------------------------------------------------------------- #
# declarations
# --------------------------------------------------------------------------- #


def test_every_backend_publishes_the_frozen_declarations() -> None:
    for backend_id in BACKEND_IDS:
        document = declaration_for(backend_id).to_document()
        for field in DECLARED_FIELDS:
            assert field in document, f"{backend_id} does not declare {field}"


def test_backend_operation_set_is_the_frozen_set() -> None:
    assert set(BACKEND_OPERATIONS) == {
        "discover", "plan", "fetch", "verify", "stage", "commit", "expose", "inspect", "rollback",
    }
    # `fake_fixture` is the deterministic P1 simulation (its bytes come from cache/fixtures and
    # the golden corpus is pinned to it); it drives the same states through SimulationRunner.
    # Every *artifact* backend must implement the frozen set itself.
    for backend_id in ("portable_file", "https_artifact"):
        backend = resolve_backend(backend_id, root=Path("."))
        for operation in BACKEND_OPERATIONS:
            assert callable(getattr(backend, operation)), f"{backend_id} has no {operation}()"


def test_a_backend_may_not_declare_source_mutation() -> None:
    """Deleting or moving the user's file must be an explicit, separately approved plan step."""

    with pytest.raises(AirootError) as caught:
        BackendDeclaration(backend_id="sneaky", source_mutation="delete")

    assert caught.value.reason_code == "UNSUPPORTED_BACKEND"
    assert any("never delete" in item for item in caught.value.evidence)


def test_script_execution_disqualifies_the_low_risk_path() -> None:
    backend = BackendDeclaration(backend_id="scripty", executes_scripts=True)

    assert backend.low_risk_eligible is False
    assert declaration_for("https_artifact").low_risk_eligible is True


def test_T013_an_irreversible_backend_is_never_on_the_low_risk_path() -> None:
    """A backend that declares it cannot be undone must need an explicit approval (T-013).

    The declaration exists for this: `reversible` is one of the nine frozen fields, and the whole
    point of declaring it is that the low-risk automatic path answers "may this run without a human?"
    — for an irreversible step the answer has to be no, so the step falls back to an explicit token
    instead of being silently auto-approved. The `executes_scripts` input to the same predicate was
    the only one watched before §61's audit of T-013, which had filed this scenario as
    `p4-real-backend`; the judgement needs no real artifact, only the declaration.
    """

    backend = BackendDeclaration(backend_id="uninstaller", reversible=False)

    assert backend.low_risk_eligible is False
    assert backend.to_document()["reversible"] is False
    # ...and the declaration is what the plan-side judgement reads, not a private flag.
    assert declaration_for("portable_file").low_risk_eligible is True


def test_an_unknown_backend_is_refused_with_the_known_list() -> None:
    with pytest.raises(AirootError) as caught:
        resolve_backend("pip", root=Path("."))
    assert caught.value.reason_code == "UNSUPPORTED_BACKEND"
    assert "portable_file" in " ".join(caught.value.evidence)


@pytest.mark.parametrize("name", ["install.bat", "setup.cmd", "run.ps1", "bootstrap.sh"])
def test_script_payloads_are_refused(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("echo hi", encoding="utf-8")

    with pytest.raises(AirootError) as caught:
        assert_script_free(path)
    assert caught.value.reason_code == "UNSUPPORTED_BACKEND"


def test_an_executable_payload_is_allowed(tmp_path: Path) -> None:
    """A single-file executable is a payload, not an installer — jq.exe is the canonical case."""

    path = tmp_path / "jq.exe"
    path.write_bytes(b"MZ-payload")
    assert_script_free(path) is None


def test_a_real_plan_requires_a_sha256_digest(registry, clock, root, artifact: Path) -> None:
    backend = resolve_backend("portable_file")

    with pytest.raises(AirootError) as caught:
        create_artifact_plan(
            registry, backend,
            capability_id="fake-tool", version="2.0.0", kind="managed_tool",
            locator=str(artifact), source_digest="md5:abc",
        )

    assert caught.value.reason_code == "INVALID_PLAN"
    assert "digest" in caught.value.message


# --------------------------------------------------------------------------- #
# the transaction
# --------------------------------------------------------------------------- #


def test_a_local_artifact_reaches_the_store_and_leaves_the_source_alone(
    registry, clock, root, artifact: Path
) -> None:
    before = artifact.read_bytes()

    tx, plan, _backend = install(registry, clock, root, artifact)

    assert tx["state"] == "FINALIZED", tx
    instance_id = plan["target"]["instance_id"]
    row = registry.instance(instance_id)
    store = Path(root.path) / "store" / instance_id
    assert row is not None
    assert row["install_backend_id"] == "portable_file"
    assert row["lifecycle_status"] == "active"
    assert store.is_dir() and (store / "payload.bin").read_bytes() == before
    assert artifact.read_bytes() == before, "the backend never mutates the source"
    assert (Path(root.path) / "store" / instance_id / "payload.bin").is_file()


def test_the_plan_records_the_backend_and_its_declarations(registry, clock, artifact: Path) -> None:
    backend = resolve_backend("portable_file")
    plan = create_artifact_plan(
        registry, backend,
        capability_id="fake-tool", version="2.0.0", kind="managed_tool",
        locator=str(artifact), source_digest=sha256_file(artifact),
    )

    assert plan["metadata"]["backend_id"] == "portable_file"
    assert plan["metadata"]["backend"]["executes_scripts"] is False
    assert plan["metadata"]["backend"]["source_mutation"] == "none"
    assert plan["source"]["integrity"]["artifact_digest"] == sha256_file(artifact)
    assert plan["operations"][0]["kind"] == "fetch"


def test_a_tampered_artifact_is_refused_before_the_store(registry, clock, root, artifact: Path) -> None:
    """The digest in the plan is the proof; bytes that changed after planning must not install."""

    tx, plan, _backend = install(registry, clock, root, artifact, digest="sha256:" + "0" * 64)

    assert tx["state"] == "ROLLED_BACK"
    assert tx["outcome"] == "DIGEST_MISMATCH"
    assert not (Path(root.path) / "store" / plan["target"]["instance_id"]).exists()


def test_a_missing_artifact_fails_without_leaving_a_stage(registry, clock, root, tmp_path: Path) -> None:
    tx, _plan, _backend = install(
        registry, clock, root, tmp_path / "not-there.bin", digest="sha256:" + "0" * 64
    )

    assert tx["state"] == "ROLLED_BACK"
    assert tx["outcome"] == "NOT_FOUND"


def test_a_refusal_names_the_backend_that_refused(registry, clock, root, tmp_path: Path) -> None:
    """§171 ②: `portable_archive` admits a local `.zip` too, and borrowed `portable_file.fetch`.

    It therefore refused with ``portable_file takes an existing local file as its source`` while the
    plan it was refusing recorded ``backend_id=portable_archive`` — the message named a backend that
    was not doing the reading, and an agent reading the evidence would go and look at the wrong
    declaration. The reader takes the name of the backend that runs it now, and this pins both
    directions: the archive backend says its own name, and the file backend still says its own.
    """

    missing = tmp_path / "cmake-3.30.5-missing.zip"

    def failure_details(backend_id: str) -> list[str]:
        backend = resolve_backend(backend_id)
        plan = create_artifact_plan(
            registry, backend,
            capability_id="fake-tool", version="3.30.5", kind="managed_tool",
            locator=str(missing), source_digest="sha256:" + "a" * 64, clock=clock,
        )
        assert plan["metadata"]["backend_id"] == backend_id
        fake_issuer.install_keyring(root.path)
        token = fake_issuer.issue(plan, clock=clock)
        tx = ArtifactRunner(registry, backend, clock=clock).commit(plan, token)
        assert tx["state"] == "ROLLED_BACK" and tx["outcome"] == "NOT_FOUND", tx
        return [str(item["detail"]) for item in tx["failure"]["evidence"]]

    archive = " ".join(failure_details("portable_archive"))
    assert "portable_archive takes an existing local file as its source" in archive, archive
    assert "portable_file" not in archive, archive

    # Non-vacuity: the backend that really is the single-file reader still says so.
    single = " ".join(failure_details("portable_file"))
    assert "portable_file takes an existing local file as its source" in single, single


def test_the_instance_digest_describes_the_owned_payload_not_the_source(
    registry, clock, root, artifact: Path
) -> None:
    """`doctor` re-checks the store payload, so its digest must be the tree digest of the store.

    The source digest and the payload digest answer different questions; collapsing them would
    make "the payload changed after installation" undetectable for real artifacts.
    """

    _tx, plan, _backend = install(registry, clock, root, artifact)
    row = registry.instance(plan["target"]["instance_id"])

    assert row["artifact_digest"] != plan["source"]["integrity"]["artifact_digest"]
    assert row["artifact_digest"].startswith("sha256:")

    from airoot.caps.doctor import diagnostics_by_code, doctor

    document = doctor(root.path, registry=registry, data_roots=False)
    assert "PAYLOAD_MISSING" not in diagnostics_by_code(document)
    assert "MANIFEST_DIGEST_MISMATCH" not in diagnostics_by_code(doctor(root.path, registry=registry, verify=True, data_roots=False))


class _NoSpaceBackend(PortableFileBackend):
    """The real backend, with the disk going full during the stage step (T-004)."""

    def stage(self, artifact, *, stage_dir: Path) -> Path:
        raise OSError(28, "No space left on device")


def test_T004_a_full_disk_during_stage_keeps_the_old_binding_and_reports_the_failure(
    registry, clock, root, artifact: Path, tmp_path: Path
) -> None:
    """T-004: the filesystem refuses a step; the old generation must survive and the stage go away.

    Draft §62 measured the before-state with exactly this backend: the raw `OSError` escaped
    `commit()`. No failed transaction was recorded, so the journal kept a **non-terminal** row that
    the next `doctor` would report as pending recovery, and the stage directory stayed on disk —
    even though every backend declares `failure_cleanup="stage_only"` and
    `TransactionJournal.discard_stage` had existed all along with no caller.
    """

    first_tx, first_plan, _ = install(registry, clock, root, artifact)
    assert first_tx["state"] == "FINALIZED"
    first_active = registry.bindings(active_only=True)
    assert len(first_active) == 1

    backend = _NoSpaceBackend()
    plan = create_artifact_plan(
        registry,
        backend,
        capability_id="fake-tool",
        version="3.0.0",
        kind="managed_tool",
        locator=str(artifact),
        source_digest=sha256_file(artifact),
        clock=clock,
        plan_id="plan/real/no-space",
    )
    token = fake_issuer.issue(plan, clock=clock)
    tx = ArtifactRunner(registry, backend, clock=clock).commit(plan, token)

    assert tx["state"] == "ROLLED_BACK" or tx["state"] == "FAILED"
    assert tx["outcome"] == "INSTALL_IO_FAILED"
    assert tx["failure"]["code"] == "INSTALL_IO_FAILED"
    evidence = " ".join(str(item.get("text", item)) for item in tx["failure"]["evidence"])
    assert "No space left on device" in evidence or "errno=28" in evidence, evidence
    assert "stage_cleaned=True" in evidence, evidence

    # The old generation is untouched: still one active binding, and it is the first one.
    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert active[0]["instance_id"] == first_plan["target"]["instance_id"]
    assert active[0]["instance_id"] == first_active[0]["instance_id"]
    assert registry.integrity_problems() == []

    # ...and nothing is left asking for recovery: a failure is a finished transaction, not a pending one.
    assert registry.transactions(unfinished_only=True) == []
    assert not (Path(root.path) / "tx" / str(tx["transaction_id"]).replace("/", "_") / "stage").exists()


def test_T005_a_refused_commit_rolls_back_and_does_not_leave_a_pending_transaction(
    registry, clock, root, artifact: Path
) -> None:
    """T-005: a filesystem refusal *at the commit step* — after the payload moved — must roll back.

    The runnable version of "the target file is locked": `store` exists as a plain file, so the
    backend's `mkdir` raises `FileExistsError`. Measured before the fix: the exception escaped
    `commit()`, the journal kept the transaction at `STAGED`, and the binding was never switched back.
    Driving it through the runner (not the backend in isolation) is the point — the backend raising is
    fine and expected; what was missing is the *runner* turning that into a diagnosis and a rollback.
    """

    first_tx, first_plan, _ = install(registry, clock, root, artifact)
    assert first_tx["state"] == "FINALIZED"

    store = Path(root.path) / "store"
    for item in sorted(store.rglob("*"), reverse=True):
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            item.rmdir()
    store.rmdir()
    store.write_text("not a directory\n", encoding="utf-8")
    try:
        second_source = artifact.parent / "payload-v2.bin"
        second_source.write_bytes(b"AIROOT-REAL-ARTIFACT-V2\n")
        tx, _plan, _backend = install(registry, clock, root, second_source, version="4.0.0")
    finally:
        store.unlink(missing_ok=True)

    assert tx["outcome"] == "INSTALL_IO_FAILED", tx
    assert tx["state"] in {"FAILED", "ROLLED_BACK"}
    evidence = " ".join(str(item.get("text", item)) for item in tx["failure"]["evidence"])
    assert "stage_cleaned=" in evidence, evidence

    active = registry.bindings(active_only=True)
    assert len(active) == 1
    assert active[0]["instance_id"] == first_plan["target"]["instance_id"], "the old generation must win"
    assert registry.integrity_problems() == []
    assert registry.transactions(unfinished_only=True) == [], (
        "a raw OSError used to leave the transaction at STAGED, i.e. pending recovery for a failure "
        "that had already been handled"
    )


def test_a_where_lookup_finds_the_real_artifact(registry, clock, root, artifact: Path) -> None:
    _tx, plan, _backend = install(registry, clock, root, artifact)

    from airoot.caps.where import WhereQuery, where

    document = where(
        registry, WhereQuery(capability_id="fake-tool"), root=root.path,
        process_entries=[], machine_entries=[], user_entries=[],
    )
    assert document["found"] is True
    assert document["management"] == "managed"
    assert document["instance_id"] == plan["target"]["instance_id"]


def test_retire_and_gc_collect_the_real_payload(registry, clock, root, artifact: Path) -> None:
    """§19's deletion grading must work on a real owned payload, not only on the fixture."""

    from airoot.caps.lifecycle import apply_gc_plan, build_gc_plan, retire

    _tx, plan, _backend = install(registry, clock, root, artifact)
    instance_id = plan["target"]["instance_id"]
    store = Path(root.path) / "store" / instance_id

    retire(registry, instance_id, clock=clock)
    gc_plan = build_gc_plan(registry, instance_id, clock=clock, root=root.path)
    token = fake_issuer.issue(gc_plan, clock=clock)
    document = apply_gc_plan(registry, gc_plan, token, root=root.path, clock=clock)

    assert document["payload_removed"] is True
    assert not store.exists()
    assert registry.instance(instance_id)["collected_at"] is not None
    assert artifact.is_file(), "collecting the payload must not touch the source"


# --------------------------------------------------------------------------- #
# https
# --------------------------------------------------------------------------- #


class _Server:
    """A loopback HTTP server. Never leaves 127.0.0.1 and is torn down by the fixture."""

    def __init__(self, payload: bytes, *, redirect_to: str | None = None) -> None:
        self.payload = payload
        self.redirect_to = redirect_to
        handler = self._handler()
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/payload.bin"

    def _handler(self):
        payload = self.payload
        redirect_to = self.redirect_to

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if redirect_to is not None:
                    self.send_response(302)
                    self.send_header("Location", redirect_to)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:  # keep the test output clean
                return

        return Handler

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def loopback():
    servers: list[_Server] = []

    def start(payload: bytes, *, redirect_to: str | None = None) -> _Server:
        server = _Server(payload, redirect_to=redirect_to)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


# --------------------------------------------------------------------------- #
# portable_archive (draft §144)
# --------------------------------------------------------------------------- #


def _archive(path: Path, members: dict[str, bytes], *, link: str | None = None) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        if link is not None:
            import stat as stat_module

            info = zipfile.ZipInfo(link)
            info.external_attr = (stat_module.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
    return path


def _artifact(path: Path) -> Artifact:
    return Artifact(path=path, digest="sha256:" + "0" * 64, size=path.stat().st_size)


def test_the_archive_backend_strips_one_wrapper_and_puts_the_entry_first(tmp_path: Path) -> None:
    """A release archive ships inside a versioned directory; a payload tree can hold several exes.

    Both halves matter: the wrapper must not become part of every entrypoint, and the capability's
    declared entry (`cmake.exe`, from the frozen list) must be `expose[0]` — that index is what
    `run --capability` executes.
    """

    from airoot.caps.backends.portable_archive import PortableArchiveBackend

    source = _archive(
        tmp_path / "cmake-3.31.6-windows-x86_64.zip",
        {
            "cmake-3.31.6-windows-x86_64/bin/cmake.exe": b"MZ cmake\n",
            "cmake-3.31.6-windows-x86_64/bin/cpack.exe": b"MZ cpack\n",
            "cmake-3.31.6-windows-x86_64/share/cmake.txt": b"docs\n",
        },
    )
    backend = PortableArchiveBackend(entry_name="cmake.exe")

    backend.stage(_artifact(source), stage_dir=tmp_path / "stage")
    store = backend.commit(tmp_path / "stage", store_dir=tmp_path / "store")

    assert (store / "bin" / "cmake.exe").is_file()
    assert backend.expose(store) == ["bin/cmake.exe", "bin/cpack.exe"]
    assert backend.inspect(store)["health"] == "healthy"


def test_the_archive_backend_refuses_traversal_and_links(tmp_path: Path) -> None:
    """Refusal, not sanitisation: a member that escapes, and a member that is a pointer."""

    from airoot.caps.backends.portable_archive import PortableArchiveBackend

    escaping = _archive(tmp_path / "escaping.zip", {"../escape.exe": b"MZ\n"})
    with pytest.raises(AirootError) as traversal:
        PortableArchiveBackend().stage(_artifact(escaping), stage_dir=tmp_path / "stage-a")
    assert traversal.value.reason_code == "UNSUPPORTED_BACKEND"
    assert "traversal" in traversal.value.message

    linked = _archive(tmp_path / "linked.zip", {"bin/cmake.exe": b"MZ\n"}, link="bin/link.exe")
    with pytest.raises(AirootError) as link:
        PortableArchiveBackend().stage(_artifact(linked), stage_dir=tmp_path / "stage-b")
    assert "non-regular" in link.value.message


def test_the_archive_backend_refuses_an_installer_but_admits_a_shim(tmp_path: Path) -> None:
    """The rule is name-scoped on purpose: `install.sh` is an installer, `npm.cmd` is a payload.

    A suffix-scoped rule would refuse every Windows runtime archive, which is why the two cases are
    asserted together — this pair is what keeps the rule honest.
    """

    from airoot.caps.backends.portable_archive import PortableArchiveBackend

    installer = _archive(tmp_path / "installer.zip", {"install.sh": b"#!/bin/sh\n", "bin/tool.exe": b"MZ\n"})
    with pytest.raises(AirootError) as refused:
        PortableArchiveBackend().stage(_artifact(installer), stage_dir=tmp_path / "stage-c")
    assert "installer script" in refused.value.message

    shimmed = _archive(tmp_path / "shim.zip", {"npm.cmd": b"@echo off\n", "bin/node.exe": b"MZ\n"})
    payload = PortableArchiveBackend(entry_name="node.exe").stage(
        _artifact(shimmed), stage_dir=tmp_path / "stage-d"
    )
    assert (payload / "bin" / "node.exe").is_file()


def test_the_archive_backend_is_bounded_and_refuses_a_non_zip(tmp_path: Path, monkeypatch) -> None:
    """The budgets are data; the point is that they are enforced against real bytes."""

    from airoot.caps.backends import portable_archive
    from airoot.caps.backends.portable_archive import PortableArchiveBackend

    not_an_archive = tmp_path / "release.zip"
    not_an_archive.write_bytes(b"this is not a zip at all\n")
    with pytest.raises(AirootError) as refused:
        PortableArchiveBackend().stage(_artifact(not_an_archive), stage_dir=tmp_path / "stage-e")
    assert "not a zip archive" in refused.value.message

    monkeypatch.setattr(portable_archive, "MAX_UNCOMPRESSED_BYTES", 8)
    oversize = _archive(tmp_path / "oversize.zip", {"bin/tool.exe": b"MZ" + b"x" * 64})
    with pytest.raises(AirootError) as bounded:
        PortableArchiveBackend().stage(_artifact(oversize), stage_dir=tmp_path / "stage-f")
    assert "unpacks to more than" in bounded.value.message


def test_https_backend_refuses_plaintext(loopback, tmp_path: Path) -> None:
    server = loopback(b"payload")
    backend = HttpsArtifactBackend()

    with pytest.raises(AirootError) as caught:
        backend.fetch(locator=server.url, destination=tmp_path / "out.bin")

    assert caught.value.reason_code == "PROVENANCE_FAILED"
    assert "https" in caught.value.message
    assert not (tmp_path / "out.bin").exists()


def test_https_backend_refuses_a_downgrading_redirect(loopback, tmp_path: Path) -> None:
    server = loopback(b"payload", redirect_to="http://127.0.0.1:1/payload.bin")
    backend = HttpsArtifactBackend()

    with pytest.raises(AirootError) as caught:
        backend.fetch(locator=server.url, destination=tmp_path / "out.bin")

    assert caught.value.reason_code == "PROVENANCE_FAILED"
    assert not (tmp_path / "out.bin").exists()


def test_https_verify_compares_the_digest(tmp_path: Path) -> None:
    """`verify` is pure: it needs no network and no success path to be meaningful."""

    from airoot.caps.backends.base import Artifact

    path = tmp_path / "payload.bin"
    payload = b"downloaded bytes"
    path.write_bytes(payload)
    backend = HttpsArtifactBackend()
    artifact = Artifact(path=path, digest="sha256:" + hashlib.sha256(payload).hexdigest(), size=len(payload))

    good = backend.verify(artifact, expected_digest="sha256:" + hashlib.sha256(payload).hexdigest())
    assert good.ok is True

    bad = backend.verify(artifact, expected_digest="sha256:" + "1" * 64)
    assert bad.ok is False
    with pytest.raises(AirootError) as caught:
        bad.require_ok(expected="sha256:" + "1" * 64)
    assert caught.value.reason_code == "DIGEST_MISMATCH"


def test_https_backend_refuses_a_script_url(tmp_path: Path) -> None:
    backend = HttpsArtifactBackend()

    with pytest.raises(AirootError) as caught:
        backend.fetch(locator="https://example.invalid/install.ps1", destination=tmp_path / "x")

    assert caught.value.reason_code == "UNSUPPORTED_BACKEND"


class _InterruptedResponse:
    """A response whose body stops partway with a real transport error (T-001)."""

    def __init__(self, first: bytes, error: BaseException) -> None:
        self._first = first
        self._error = error

    def read(self, amount: int) -> bytes:
        if self._first:
            chunk, self._first = self._first, b""
            return chunk
        raise self._error

    def __enter__(self) -> "_InterruptedResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _InterruptedOpener:
    def __init__(self, response: _InterruptedResponse) -> None:
        self._response = response

    def open(self, request: object, timeout: int | None = None) -> _InterruptedResponse:  # noqa: ARG002
        return self._response


def test_T001_an_interrupted_download_is_diagnosed_and_leaves_nothing_behind(tmp_path: Path) -> None:
    """T-001: a download that dies mid-stream must be *diagnosed*, and leave no partial file.

    The injected opener is how this is reachable without a network, and the interesting error is the
    one a real truncated transfer produces: when the server declares `Content-Length: N` and sends
    fewer bytes, `urllib` raises `http.client.IncompleteRead`, which subclasses **neither** `OSError`
    nor `URLError`. Draft §62 measured that before the fix: the raw exception escaped `fetch` and —
    because the cleanup branch never ran — the half-written file stayed on disk. Both halves of the
    scenario failed, and no test had ever driven the streaming loop: the loopback-server tests above
    are refused by the scheme guard long before a byte is read.
    """

    import http.client

    payload = b"x" * 4096
    cases = {
        "transport reset": ConnectionResetError(10054, "connection reset by peer"),
        "truncated body": http.client.IncompleteRead(payload[:1024], 64),
    }
    for label, error in cases.items():
        destination = tmp_path / f"{label.replace(' ', '-')}.bin"
        backend = HttpsArtifactBackend(
            opener=_InterruptedOpener(_InterruptedResponse(payload, error))
        )

        with pytest.raises(AirootError) as caught:
            backend.fetch(locator="https://example.invalid/payload.bin", destination=destination)

        assert caught.value.reason_code == "INSTALL_IO_FAILED", label
        assert caught.value.exit_code == 2, "a broken transfer is not an invalid plan (exit 7)"
        assert not destination.exists(), f"{label}: the partial file was left behind"
        assert any("bytes received" in item for item in caught.value.evidence), label


def test_the_cli_dispatches_install_to_the_plan_s_backend(
    capsys, registry, clock, root, artifact: Path
) -> None:
    """`install` must pick the driver from the plan, not always assume the fixture."""

    from airoot.cli import main
    from airoot.clock import SYSTEM_CLOCK

    # The CLI runs on the system clock, so the plan and token must too or the approval is
    # legitimately expired by the time `install` checks it.
    backend = resolve_backend("portable_file")
    plan = create_artifact_plan(
        registry, backend,
        capability_id="fake-tool", version="3.0.0", kind="managed_tool",
        locator=str(artifact), source_digest=sha256_file(artifact), clock=SYSTEM_CLOCK,
        plan_id="plan/cli/real",
    )
    fake_issuer.install_keyring(root.path)
    token = fake_issuer.issue(plan, clock=SYSTEM_CLOCK)
    cli_root = Path(registry.path).parent.parent
    plans = cli_root / "state" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    plan_file = plans / "plan_cli_real.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    token_file = cli_root / "state" / "token.json"
    token_file.write_text(json.dumps(token), encoding="utf-8")

    code = main(
        ["--json", "--root", str(cli_root), "install", str(plan_file), "--token-file", str(token_file)]
    )
    document = json.loads(capsys.readouterr().out)

    assert code == 0, document
    assert document["state"] == "FINALIZED"
    row = registry.instance(plan["target"]["instance_id"])
    assert row["install_backend_id"] == "portable_file"
    assert artifact.is_file(), "the source is never consumed"
