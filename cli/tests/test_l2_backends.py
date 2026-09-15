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
    BACKEND_OPERATIONS,
    DECLARED_FIELDS,
    BackendDeclaration,
    assert_script_free,
    declaration_for,
    resolve_backend,
    sha256_file,
)
from airoot.caps.backends.https_artifact import HttpsArtifactBackend
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
