"""Running a managed payload **once** (ADR-0047).

The minimum version's definition asks for one thing this build did not have: **a verb that really
starts the payload AIROOT installed and reports what happened** (judgement 9 in
``docs/AIROOT-最小版本-v1.md``). Draft §118 did that by hand, with an absolute path and no ledger
entry. This module is the resolution half of making it a verb; `cli.cmd_run` is the other half.

What it deliberately does **not** do is in ADR-0047, and each omission has a reason rather than a
preference:

* **no environment or PATH injection** — the frozen contract allows exactly one AIROOT entry in the
  machine PATH (AGENTS.md §5 item 8) and forbids persisted values that point at a shim (draft §13.2).
  The entrypoint is passed as an **absolute path**, so "which copy ran" is visible in the command
  itself instead of being decided by PATH order;
* **no digest re-verification** — that is ``tool verify``'s job (it recomputes the tree digest and
  reports without repairing). The digest in the report is the **registered** value, and the document
  says so in a field of its own, because a reader must not mistake it for a fresh measurement;
* **no policy about ``retired`` / ``broken``** — those are reported facts, not refusals. Naming an
  instance by id is the caller's decision; hiding a payload that can still run would be worse than
  running it and saying what state it is in.

Refusals are therefore only about **facts that make the run impossible**: not registered, not owned
by AIROOT, or the payload/entrypoint is gone (a collected instance lands here, which is where "已回收
要明确拒绝" is actually enforced).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..exits import AirootError
from ..paths import from_root_relative
from ..registry.entities import load_json
from .lifecycle import find_target

#: How much of a child's output is kept, and **why the tail**: a runaway child must not be able to
#: make the CLI unusable, and the end of the stream is what a reader needs (a stack trace ends
#: there). Same rule and same limit as `exec`'s child output.
OUTPUT_LIMIT = 65536

#: The entrypoint this verb runs. The registry may declare several (a payload can expose a CLI and a
#: server); the **first** one is the main entrypoint by construction — `backend.expose` order is the
#: producer's, and `managed-tool-instance.entrypoints` is that same tuple.
MAIN_ENTRYPOINT_INDEX = 0


@dataclass(frozen=True)
class RunTarget:
    """Everything needed to start one child, resolved from the registry and the filesystem."""

    instance_id: str
    capability_id: str
    version: str
    kind: str
    store_path: str
    store_dir: Path
    entrypoint: Path
    entrypoint_relative: str
    lifecycle_status: str
    health: str
    artifact_digest: str
    collected_at: str | None

    def to_document(self) -> dict[str, Any]:
        """The facts about *what* is about to run, before any child exists."""

        return {
            "instance_id": self.instance_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "kind": self.kind,
            "store_path": self.store_path,
            "entrypoint": str(self.entrypoint),
            "entrypoint_relative": self.entrypoint_relative,
            "lifecycle_status": self.lifecycle_status,
            "health": self.health,
            "payload_digest": self.artifact_digest,
            "payload_digest_source": "registry",
            "collected_at": self.collected_at,
        }


def resolve_run_target(registry: Any, root: Path, target: str) -> RunTarget:
    """Resolve ``target`` to a runnable payload, or refuse with the fact that is missing.

    Ownership is decided by ``caps.lifecycle.find_target`` — the same function `retire`/`gc`/`uninstall`
    use — so "AIROOT owns this" cannot come to mean two different things in two places.
    """

    kind, row = find_target(registry, target)
    if kind == "reference":
        raise AirootError(
            "OWNERSHIP_REQUIRED",
            f"{row['external_id']} is an external reference; this verb runs payloads AIROOT installed",
            evidence=[
                f"absolute path: {row['path']}",
                "AIROOT records references but does not manage their lifecycle",
                f"suggestion: airoot exec {row['external_id']} -- <command>   # the reference-side verb",
            ],
        )
    if kind != "owned":
        raise AirootError(
            "OWNERSHIP_REQUIRED",
            f"{row['instance_id']} is not an AIROOT-owned payload (store_path={row['store_path']})",
            evidence=["only payloads under store/ with a non-external install backend may be run by id"],
        )

    instance_id = str(row["instance_id"])
    entrypoints = [str(item) for item in load_json(row["entrypoints_json"], [])]
    store_path = str(row["store_path"])
    store_dir = from_root_relative(store_path, Path(root))

    if not entrypoints:
        # A registered instance with no declared entrypoint cannot be started, and guessing one out of
        # the payload's files would be exactly the kind of guess this project refuses.
        raise AirootError(
            "PAYLOAD_MISSING",
            f"{instance_id} declares no entrypoint, so there is nothing to run",
            evidence=[f"store_path={store_path}", "the registry's entrypoints list is empty"],
        )

    entrypoint_relative = entrypoints[MAIN_ENTRYPOINT_INDEX]
    entrypoint = store_dir / Path(entrypoint_relative)

    if row["collected_at"]:
        raise AirootError(
            "PAYLOAD_MISSING",
            f"the payload of {instance_id} was collected; there is nothing left to run",
            evidence=[
                f"collected_at={row['collected_at']}",
                f"store_path={store_path}",
                "collecting deletes the payload and keeps the registry row for binding history",
            ],
        )
    if not store_dir.is_dir():
        raise AirootError(
            "PAYLOAD_MISSING",
            f"{store_path} does not exist",
            evidence=[f"instance_id={instance_id}", "the payload is gone but the instance is still declared"],
        )
    if not entrypoint.is_file():
        raise AirootError(
            "PAYLOAD_MISSING",
            f"the entrypoint this instance declares is missing: {entrypoint}",
            evidence=[
                f"declared entrypoints: {', '.join(entrypoints)}",
                f"store_path={store_path}",
            ],
        )

    return RunTarget(
        instance_id=instance_id,
        capability_id=str(row["capability_id"]),
        version=str(row["version"]),
        kind=str(row["kind"]),
        store_path=store_path,
        store_dir=store_dir,
        entrypoint=entrypoint,
        entrypoint_relative=entrypoint_relative,
        lifecycle_status=str(row["lifecycle_status"]),
        health=str(row["health"]),
        artifact_digest=str(row["artifact_digest"]),
        collected_at=row["collected_at"],
    )


def run_once(target: RunTarget, arguments: list[str], *, capture: bool) -> dict[str, Any]:
    """Start the payload once and report its status and output **as they came back**.

    A child that exits non-zero is *reported*, not raised: the request was fine and the payload ran,
    which is what ``CHILD_PROCESS_FAILED`` says (exit code 2, the same code `exec` uses). A child that
    cannot be started **at all** — not a valid executable, refused by the OS — is the same verdict for
    the caller but carries the operating system's own words as evidence; the draft §62 lesson is that
    a bare `OSError` escaping as a traceback tells the caller nothing.
    """

    document: dict[str, Any] = dict(target.to_document())
    document["schema_version"] = 1
    command = [str(target.entrypoint), *arguments]
    document["command"] = command
    document["persisted"] = False

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=capture,
            text=capture,
            encoding="utf-8" if capture else None,
            errors="replace" if capture else None,
        )
    except OSError as error:
        raise AirootError(
            "CHILD_PROCESS_FAILED",
            f"the payload could not be started: {target.entrypoint}",
            evidence=[
                f"{type(error).__name__}: {error}",
                f"errno={getattr(error, 'errno', None)} winerror={getattr(error, 'winerror', None)}",
                f"the file exists: {target.entrypoint.is_file()}",
                "the payload is present but this machine refused to start it",
            ],
        ) from error

    document["exit_status"] = completed.returncode
    document["reason_code"] = "SUCCESS" if completed.returncode == 0 else "CHILD_PROCESS_FAILED"
    if capture:
        document["stdout"] = _bounded(completed.stdout or "")
        document["stderr"] = _bounded(completed.stderr or "")
    return document


def _bounded(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return "...[truncated]...\n" + text[-OUTPUT_LIMIT:]


__all__ = ["MAIN_ENTRYPOINT_INDEX", "OUTPUT_LIMIT", "RunTarget", "resolve_run_target", "run_once"]
