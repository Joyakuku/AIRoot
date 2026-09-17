"""Which capability implementations this build can actually run.

``extension status <id>`` must answer with **that extension's own** facts. A manifest is a
declaration: it may name any implementation it likes, and this build hosts exactly one of them —
the deterministic fake extension in ``fake.py``. For every other manifest the honest answer is
"this build cannot run it", because the alternative is what an unconditional
``FakeExtension(manifest)`` produces: an envelope whose ``extension_id`` says one thing while its
``data`` and ``evidence`` are the *fake host's* hardcoded self-test word. That is a report telling
someone else's story.

The criterion is the manifest's own ``implementation_id``, never the extension's name: a module
may rename itself without changing what would run it, so a name test is a coincidence rather than
a criterion.

``file_search`` is the case that matters today — it is not an extension host in this build at all.
It is ``caps/search.py``, reached through the ``search`` verbs.
"""

from __future__ import annotations

from typing import Any

#: The implementation ``ext/fake.py`` really is, and the only one this build can host.
HOSTED_IMPLEMENTATION_ID = "airoot-fake-deterministic"

#: Where a capability declared by an *unhostable* manifest is really implemented here, keyed by the
#: manifest's own ``capability_types``. A capability missing from this table is one this build does
#: not reach through an extension host, and the evidence says exactly that instead of inventing a
#: path for it — claiming "no implementation exists" would be the same defect one size smaller.
CAPABILITY_HOMES: dict[str, dict[str, str]] = {
    "file_search": {
        "module": "cli/app/airoot/caps/search.py",
        "status_command": "search status",
        "implementations_command": "search implementations",
    },
}


def hosted_here(manifest: dict[str, Any]) -> bool:
    """Whether this build can run the implementation ``manifest`` declares."""

    return str(manifest.get("implementation_id", "")) == HOSTED_IMPLEMENTATION_ID


def unhostable_evidence(manifest: dict[str, Any]) -> list[str]:
    """Evidence naming where a manifest's capabilities really live in this build.

    Derived from the manifest's own ``implementation_id`` and ``capability_types``, so a module that
    renames itself or declares a different implementation changes the answer with no string edited
    here.
    """

    declared = str(manifest.get("implementation_id", "(none declared)"))
    evidence = [
        f"implementation_id={declared} is not the implementation this build hosts "
        f"({HOSTED_IMPLEMENTATION_ID}, ext/fake.py); this build has no host process for it"
    ]
    for capability in sorted(str(item) for item in manifest.get("capability_types", [])):
        home = CAPABILITY_HOMES.get(capability)
        if home is None:
            evidence.append(
                f"capability {capability} is not reachable through an extension host in this build; "
                "there is nothing to ask it here"
            )
            continue
        evidence.append(
            f"capability {capability} is implemented in {home['module']}, not through an extension host"
        )
        evidence.append(
            f"ask it directly: airoot {home['implementations_command']} / airoot {home['status_command']}"
        )
    return evidence
