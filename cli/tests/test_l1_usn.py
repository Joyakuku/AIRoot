"""L1: the USN capability probe (draft §34, ADR-0020).

The probe exists so that "why is there no fast index?" is answerable by the tool. These tests pin
the three rules that make it safe to ship:

* **lazy** — without `query=True` it touches nothing (no volume handle, ever);
* **failures are data** — a missing volume, a non-NTFS filesystem or an access denial becomes fields,
  never an exception;
* **no unverified verdict** — the raw observations survive into the document, so "we could not test
  this" is never rendered as "not supported".

Every test injects a prober; the real `ctypes` path is exercised only by the opt-in command on a real
machine (and by the acceptance script), never here.
"""

from __future__ import annotations

import pytest

from airoot.caps import usn


def test_without_query_nothing_is_probed() -> None:
    def exploding(_volume: str) -> usn.UsnProbe:  # pragma: no cover - must never run
        raise AssertionError("the probe must not touch a volume unless it is asked to")

    probe = usn.probe(prober=exploding)

    assert probe.checked is False
    assert probe.journal_present is None
    assert "not probed" in probe.reason
    assert probe.to_document()["native_index_available"] is False


def test_the_default_volume_comes_from_the_system_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SystemDrive", "Q:")
    assert usn.system_volume() == "Q:\\"


def test_an_available_volume_reports_the_raw_observations() -> None:
    def prober(volume: str) -> usn.UsnProbe:
        return usn.UsnProbe(
            volume=volume,
            checked=True,
            filesystem="NTFS",
            elevated=True,
            volume_handle_access="GENERIC_READ",
            journal_present=True,
            journal_id=42,
            first_usn=100,
            next_usn=200,
            enumeration_bytes=65496,
            unprivileged_read="rejected (err=87)",
            reason="the journal is readable and the volume can be enumerated",
            observations=("journal id=42", "enumeration succeeded"),
        )

    probe = usn.probe("C:\\", query=True, prober=prober)
    document = probe.to_document()

    assert probe.native_index_available is True
    assert document["journal_id"] == 42
    assert document["enumeration_bytes"] == 65496
    # The unprivileged observation is reported verbatim instead of being turned into a boolean.
    assert document["unprivileged_read"] == "rejected (err=87)"
    assert document["elevated"] is True
    assert len(document["observations"]) == 2


def test_a_volume_without_a_journal_is_not_available() -> None:
    probe = usn.UsnProbe(
        volume="D:\\",
        checked=True,
        filesystem="exFAT",
        journal_present=False,
        reason="the change journal exists only on NTFS",
    )

    assert probe.native_index_available is False
    assert probe.to_document()["journal_present"] is False


def test_a_journal_without_enumeration_is_not_available() -> None:
    """Reading records is not enough for an index: the first full listing is the privileged part."""

    probe = usn.UsnProbe(
        volume="C:\\",
        checked=True,
        filesystem="NTFS",
        journal_present=True,
        journal_id=7,
        enumeration_bytes=None,
        reason="cannot open the volume read-only",
    )

    assert probe.native_index_available is False


def test_ctl_codes_match_the_documented_values() -> None:
    """These were confirmed against a real machine (ADR-0020); a typo here would be silent."""

    assert usn.FSCTL_QUERY_USN_JOURNAL == 0x000900F4
    assert usn.FSCTL_ENUM_USN_DATA == 0x000900B3
    assert usn.FSCTL_READ_USN_JOURNAL == 0x000900BB
    assert usn.FSCTL_READ_UNPRIVILEGED_USN_JOURNAL == 0x000900DF


def test_the_native_candidate_is_declared_in_policy_not_hard_coded() -> None:
    candidate = usn.native_candidate()

    assert candidate["implementation_id"] == "airoot-native-search-native-index"
    assert candidate["implementation_kind"] == "native"
    assert candidate["available"] is False
    assert candidate["checked"] is False
    assert candidate["reason_code"] == "SEARCH_BACKEND_UNAVAILABLE"
    assert "privileged_volume_enumeration" in candidate["requires"]
    assert candidate["probe_hint"].startswith("airoot search status --probe-native-index")


def test_the_default_probe_reports_data_even_when_the_volume_is_absent() -> None:
    """`probe(query=True)` against a drive that cannot exist must return fields, not raise."""

    probe = usn.probe("Z:\\", query=True)

    assert probe.checked is True
    assert probe.volume == "Z:\\"
    assert probe.reason
    assert probe.native_index_available is False
