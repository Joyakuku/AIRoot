"""L1: the ACL write side — baseline / apply / verify / restore (stage §113).

``caps/acl.py`` is the observation half of ADR-0023 and this file's read-only tests stay as they are.
What is added here is the other half as a **library**: a baseline value, ``apply_baseline``,
``verify_baseline`` and ``restore_acl``. Nothing in the CLI, no verb, no plan schema and no golden
fixture reaches it — without the broker there is no approved caller, and that is asserted rather than
described.

**AGENTS.md §8 read precisely, because "must not pollute the host's ACL" is about *whose* ACL.** The
prohibited thing is changing the access control list of a directory this process did not create: a
real data root, `D:\\env`, the checkout, `%TEMP%`. This module's write path *requires* writing a DACL
somewhere — a library that can only be tested by never calling it is not tested — so the reading taken
here is: a test may write an ACL **only on a directory it created itself, inside the session temp root**
(``cli/tests/.tmp/…``, removed by the session fixture), and it must **restore that directory's original
DACL and delete it before it ends, including when an assertion fails**. Every write-path test below
captures its directory's snapshot before it writes, restores it in a ``finally``, re-reads the ACL
afterwards and fails loudly if it is not back. Nothing here ever names a directory it did not create.

**The write path is protected, and its one-way cost is asserted rather than hidden.** Every write below
runs through ``apply_baseline``, which passes ``PROTECTED_DACL_SECURITY_INFORMATION``: the stored DACL
is the one given, and the parent's live inheritable ACEs stop flowing in. Two consequences this file
measures instead of assuming:

* An **empty** baseline is refused as a finding — a protected empty DACL lands and then denies the
  caller too, so it cannot be undone through this API. ``test_an_empty_baseline_is_refused_because_it_cannot_be_undone``
  asserts the refusal, the reported numbers, and that nothing was written. This replaced an earlier
  test that *wrote* the empty DACL, whose teardown could not restore or delete the directory it landed
  on: the old assertion ``['entries: recorded=0 actual=11']`` was the unprotected write re-inheriting
  the parent's 11 ACEs, and writing it protected is what turns that into a directory nothing can open.
* Protection drops the ``INHERITED_ACE`` bit (``0x10``) from every ACE it stores. "Restored" therefore
  means the **same ACEs in the same order with the same masks and SIDs and the same owner**, compared
  with that single bit masked out — asserted by ``_same_access`` and stated in each test that uses it.
  Comparing digests across a round trip would fail on any directory with inherited ACEs, by exactly
  that bit, and on this host that is every test directory.

Three properties the tests are built to keep honest:

* **apply is verified by re-reading, not by trusting the return value.** ``SetSecurityInfo`` returning
  ``ERROR_SUCCESS`` while the DACL comes back different — or absent, which grants everyone full control
  — is a measured failure mode here, not a hypothetical one, so "applied" means "read back and matched".
* **a refusal is data.** A refused write, a vanished directory and an unopenable handle all come back
  as findings naming the call and the Windows status. A physical refusal cannot be provoked through
  ``SetSecurityInfo`` on a directory this process owns, so that path is exercised through its own seam
  with the status a real refusal reports, and that substitution is stated in the test rather than
  hidden. The refusals that *can* be provoked physically — an absent DACL, an empty baseline, a deny
  ACE that takes the directory away — are provoked and asserted.
* **the one deliberate raise is pinned.** A caller passing nonsense — an ACE this module cannot write,
  an entry with no trustee, a baseline that was never observed — gets ``ValueError``, because no retry
  or privilege would change that answer.
"""

from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from airoot.caps import acl
from airoot.caps.acl import (
    AclEntry,
    AclSnapshot,
    acl_baseline,
    acl_digest,
    apply_baseline,
    capture_acl,
    differences,
    restore_acl,
    verify_baseline,
)

#: Well-known SIDs, so the tests never depend on this machine's account names and the expected ACEs stay
#: machine-independent. `S-1-5-32-545` is BUILTIN\Users, `S-1-5-11` is NT AUTHORITY\Authenticated Users
#: and `S-1-5-32-551` is BUILTIN\Backup Operators; all three are present on every Windows machine, and
#: the first two are in every `%TEMP%`-derived directory's inherited ACL.
USERS = "S-1-5-32-545"
AUTHENTICATED_USERS = "S-1-5-11"
BACKUP_OPERATORS = "S-1-5-32-551"

#: `FILE_ALL_ACCESS`, `SYNCHRONIZE`, and the `INHERITED_ACE` / container-inherit ACE flags. Named
#: because a test that spells them as bare hex cannot be read, and these are what the assertions are
#: actually about.
FILE_ALL_ACCESS = 0x001F01FF
SYNCHRONIZE = 0x00100000
ACE_INHERITED = 0x10
ACE_CONTAINER_INHERIT = 0x02
ACE_OBJECT_INHERIT = 0x01


def snapshot(entries: list[tuple[int, int, int, str]], owner: str = "S-1-5-18") -> AclSnapshot:
    """The synthetic snapshot the read-only tests already use, reused here for baselines."""

    return AclSnapshot(
        path="X:/synthetic",
        owner=owner,
        entries=tuple(AclEntry(*item) for item in entries),
        dacl_present=True,
    )


def _same_access(before: AclSnapshot, after: AclSnapshot) -> bool:
    """Do two observations describe the same access for the same principals?

    Identical ACEs in identical order with identical masks and SIDs, and the same owner — with the
    ``INHERITED_ACE`` bit masked out of the flags on both sides, and only that bit. That bit is the
    write side's one-way cost (see the module docstring in ``caps/acl.py``): a protected write stores an
    inherited ACE explicitly, so after any round trip the effective access is the same while the
    ``DACL`` digest necessarily differs. Masking it is what makes "the restore put the access back" a
    claim this host can actually check; masking anything else would be the test weakening itself.

    The distinction is not decoration: for a directory whose ACEs were all inherited, the two ACLs are
    *equal as access* and *different as bytes*, and a test that compared digests would report the
    restore as a failure when it worked.
    """

    if before.owner != after.owner:
        return False
    if len(before.entries) != len(after.entries):
        return False
    return all(
        (left.ace_type, left.flags & ~ACE_INHERITED, left.mask, left.sid)
        == (right.ace_type, right.flags & ~ACE_INHERITED, right.mask, right.sid)
        for left, right in zip(before.entries, after.entries)
    )


#: A baseline the tests impose. Built from **allow** ACEs only, and that is deliberate rather than
#: timid: a baseline that grants the account running the tests nothing takes the directory away from
#: the process that wrote it — measured on this host, a one-entry allow for `BUILTIN\\Users` alone left
#: the directory writable (`BUILTIN\\Administrators` in the token matched), while a DACL with no
#: matching allow, e.g. `allow SYSTEM` alone, made `CreateFileW(path, WRITE_DAC)` fail with
#: `ERROR_ACCESS_DENIED`(5). A round-trip test whose subject directory becomes unreachable cannot
#: restore itself, so the write/verify/restore tests use baselines that keep the caller in, and the
#: deny ACE gets its own test below.
SYNTHETIC = snapshot(
    [
        (0x00, 0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),
        (0x00, 0x00, FILE_ALL_ACCESS, USERS),
    ]
)

#: A second, different baseline, for the tests that need two distinct writes in a row.
OTHER = snapshot([(0x00, 0x00, SYNCHRONIZE, USERS)])


class WriteDirectory:
    """A directory this test created, whose original DACL is put back before the test ends.

    The guard is a ``finally`` for the reason AGENTS.md §8 gives: an assertion failure in the body must
    not be the thing that leaves a machine directory with a changed ACL. Cleanup is *checked*, not
    assumed — a restore that silently did nothing would otherwise pass as tidiness.

    **Every write this file performs is restorable, and that is asserted in one place.** No test here
    writes an empty DACL (the one posture ``apply_baseline`` refuses) and no test's deny matches this
    process, so ``restore_acl`` can always put the directory back and this fixture can always require
    it to have done so. ``restore_acl``'s own answer is still allowed to be non-empty — the first
    protected write of inherited ACEs reports the ``INHERITED_ACE`` bit it dropped — so the fixture
    re-reads and compares with :func:`_same_access` rather than trusting that answer's length.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.original = capture_acl(path)

    def put_back(self) -> tuple[list[str], AclSnapshot]:
        """Restore the captured snapshot and re-read. Never swallows anything the module reports."""

        findings = restore_acl(self.path, self.original) if self.original.observed else []
        return findings, capture_acl(self.path)


#: Gives each `writable` fixture a directory name of its own; see the fixture for why it is not shared.
_writable_counter: list[int] = []

#: Distinguishes this run's throwaway directories from any earlier run's. Not hygiene: a directory whose
#: protected DACL denies the caller cannot be deleted at all — measured, and the reason ``_delete_directory``
#: asserts — so a name reused across runs could hand a later test a directory that is not the one it
#: created, and the failure would point at the wrong thing entirely. The counter alone cannot do this,
#: because it restarts at zero every session.
_RUN_TAG = f"{os.getpid()}-{uuid4().hex[:8]}"


def _delete_directory(path: Path) -> None:
    """Remove a directory this test created, and **fail** if it is still there afterwards.

    ``shutil.rmtree(..., ignore_errors=True)`` is not enough here, and that was measured rather than
    assumed: an earlier version of this file deliberately left a target carrying a deny ACE for
    ``BUILTIN\\Users``, which is a group this process is in, so the delete was refused — silently — and
    the directory survived. A cleanup that cannot say it happened turns one failure into a confusing
    series, so this asserts instead of hoping.
    """

    shutil.rmtree(path, ignore_errors=True)
    assert not path.exists(), f"a directory this test created could not be deleted: {path}"


@pytest.fixture
def writable(tests_tmp: Path):
    """One directory per test, under the session temp root, with its ACL restored on the way out.

    Each test gets its own **throwaway parent** as well as its own target. That is not tidiness: a test
    that imposes a baseline changes the target's inheritable ACEs, so a sibling created later in the
    same parent would start from a different security descriptor — measured here as an
    `ERROR_ACCESS_DENIED` on a write that had worked a moment earlier. An isolated parent removes that
    whole class of interference, and it is why the fixture can promise the target is a directory *this
    test* created.

    The parent is removed with :func:`_delete_directory` *before* it is created and again in the
    teardown, so its name is unique to this test **and this run** (see ``_RUN_TAG``) and a leftover from
    an earlier run can never be mistaken for this test's own directory. The teardown also **fails the
    test** when the DACL is not back: the restore is attempted even when the body raised, its result is
    reported in the failure message, and the re-read is compared with :func:`_same_access`.
    """

    _writable_counter.append(len(_writable_counter))
    parent = tests_tmp / f"acl-write-{_writable_counter[-1]}-{_RUN_TAG}"
    _delete_directory(parent)
    parent.mkdir(parents=True, exist_ok=True)
    directory = WriteDirectory(parent / "target")
    directory.path.mkdir(parents=True, exist_ok=True)
    directory.original = capture_acl(directory.path)
    try:
        yield directory
    finally:
        findings, back = directory.put_back()
        _delete_directory(parent)
        assert directory.original.observed and back.observed, (
            "the test's own directory must be observable before and after: before="
            f"{directory.original.to_document()} after={back.to_document()}"
        )
        assert _same_access(directory.original, back), (
            f"the test left a changed ACL behind: restore said {findings}; before="
            f"{directory.original.to_document()} after={back.to_document()}"
        )


# --- the baseline value ---------------------------------------------------------------------------


def test_a_baseline_is_data_that_round_trips_and_compares_by_its_own_digest() -> None:
    """A plan carrying a baseline has to be approvable, so the value has to survive being written down.

    Three things are asserted together because they are one property: the value serialises, it reads
    back to the same value, and a *reordering* of the same ACEs is a different value. The last one is
    ADR-0023 decision 3 applied to the write side — Windows ACE order carries meaning, so a baseline
    that sorted its entries for tidiness would describe a different permission set.
    """

    document = SYNTHETIC.to_document()
    assert document["dacl_present"] is True
    assert document["digest"] == acl_digest(SYNTHETIC)

    restored = AclSnapshot.from_document("X:/elsewhere", document)
    assert restored.entries == SYNTHETIC.entries
    assert restored.dacl_present is True
    assert acl_digest(restored) == acl_digest(SYNTHETIC)

    reordered = snapshot(
        [
            (0x00, 0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),
            (0x01, 0x00, SYNCHRONIZE, USERS),
        ]
    )
    assert acl_digest(reordered) != acl_digest(SYNTHETIC)
    assert differences(SYNTHETIC, reordered)

    # A failed observation is not a baseline: it must not compare equal to anything, and it must not be
    # read back as "this directory grants nothing".
    unobserved = AclSnapshot(path="X:/absent", reason="GetNamedSecurityInfoW returned 2")
    assert unobserved.observed is False
    assert unobserved.to_document()["digest"] is None
    assert differences(unobserved, SYNTHETIC) == []


def test_acl_baseline_derives_a_baseline_from_the_directory_it_observes(tmp_path: Path) -> None:
    """The "derive a baseline" spelling, pinned as the observation it is."""

    observed = capture_acl(tmp_path)
    derived = acl_baseline(tmp_path)
    assert observed.observed is True
    assert derived.to_document() == observed.to_document()


# --- apply / verify / restore ---------------------------------------------------------------------


def test_capture_apply_verify_restore_verify_again_round_trips(writable: WriteDirectory) -> None:
    """The flow a protected operation would use, end to end, on a directory this test created.

    The assertion that matters is the last one: after ``restore_acl`` the directory grants exactly what
    it granted before the test wrote anything — same owner, same ACEs, same order, same masks, same SIDs
    — which is what makes "applying a baseline is not a one-way door" a fact about this code rather than
    a sentence in a docstring. The comparison masks the ``INHERITED_ACE`` bit on both sides for the
    reason :func:`_same_access` records; the *digest* is deliberately not asserted equal, because on
    this host it never can be after a protected write, and a test that claimed otherwise would be
    asserting the host's behaviour rather than this module's.

    ``restore_acl``'s own answer is allowed to be non-empty here and is checked for its *content*
    instead: the first protected write of the captured snapshot reports the inherited bit it dropped.
    """

    before = writable.original
    assert before.observed, "the test's own directory must be observable, or nothing below means anything"

    # A baseline of two explicit allow ACEs, in a chosen order, neither of them inherited from anywhere.
    applied = apply_baseline(writable.path, SYNTHETIC)

    try:
        # `applied` is `verify`'s answer for the directory as it now stands — the same list, from the
        # same comparison — so the two must agree rather than each telling its own story.
        assert applied == verify_baseline(writable.path, SYNTHETIC)

        landed = capture_acl(writable.path)
        assert landed.observed is True
        assert landed.dacl_present is True
        # The two entries that were asked for, in the order asked for, at the head of the stored DACL.
        # This is the assertion that catches an implementation which passed the ACL to the wrong
        # `SetSecurityInfo` slot: that mistake returns success and leaves the DACL absent (0 entries)
        # instead of these two.
        assert [(entry.ace_type, entry.mask, entry.sid) for entry in landed.entries[:2]] == [
            (0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),
            (0x00, FILE_ALL_ACCESS, USERS),
        ]
        assert acl_digest(landed) != acl_digest(before), "the write must actually have changed something"
        # "Applied" is not "the call returned": it is "read it back and it matched", which is what an
        # empty list from `apply_baseline` means. Imposing two explicit allow ACEs on a directory whose
        # ACEs were only inherited lands cleanly — the flag cost is pinned in
        # `..._protection_suppresses_inheritance_...`, where the baseline *is* the inherited snapshot.
        assert applied == [], applied
        # The re-read is the stronger check and is asserted on its own line; the first assertion above is
        # the weaker one — apply's answer against a second call of the same comparison — and it is kept
        # because a caller reading one and not the other must get the same list.
        assert verify_baseline(writable.path, landed) == []
    finally:
        # Even if an assertion above failed, the next line is what puts the directory back.
        restored, back = writable.put_back()

    assert _same_access(before, back), (
        f"restore did not put the access back: it said {restored}; before={before.to_document()} "
        f"after={back.to_document()}"
    )
    # `apply_baseline` never writes the owner, so a DACL round trip must not have moved it either.
    assert back.owner == before.owner
    # And the restored *observation* is a baseline the directory now matches exactly, which is what makes
    # the round trip repeatable rather than merely close.
    assert verify_baseline(writable.path, back) == []


def test_protection_suppresses_inheritance_and_says_so_once(writable: WriteDirectory) -> None:
    """The measured reason the write path is protected: without it, no baseline can ever match.

    A directory under a parent with live inheritable ACEs was, before this flag, written and then read
    back as **12** entries for a one-entry baseline on this host — the explicit ACE plus the 11 the
    parent re-applied — so ``apply_baseline`` always returned a difference and ``verify_baseline`` could
    never return an empty list. A baseline no directory can match is not a baseline, so the write
    carries ``PROTECTED_DACL_SECURITY_INFORMATION``.

    Three things are asserted together, because together they are the whole trade:

    * the stored DACL is exactly the baseline's entries, in order, and nothing inherited survives;
    * what the write *costs* is reported rather than hidden — imposing ACEs that were inherited reports
      the flag change (``0x13`` → ``0x03``) once per ACE, because ``INHERITED_ACE`` is gone for good;
    * the directory then matches its own re-observation, so the cost is paid once and the write path is
      idempotent afterwards.

    The last point is what ``test_a_baseline_derived_from_the_directory_itself_applies_and_verifies_clean``
    used to claim about the *first* write. It is true, and this is where the first write's one-way cost
    is pinned rather than left as a surprise.
    """

    before = writable.original
    assert any(entry.flags & ACE_INHERITED for entry in before.entries), (
        "this test needs a directory whose ACEs were inherited from its parent; "
        f"before={before.to_document()}"
    )

    findings = apply_baseline(writable.path, before)
    try:
        landed = capture_acl(writable.path)
        assert landed.observed is True and landed.dacl_present is True
        # Same access, and not one inherited ACE left — the parent no longer reaches into this directory.
        assert _same_access(before, landed), (
            f"the protected write changed the access, not just the flags: {landed.to_document()}"
        )
        assert not [entry for entry in landed.entries if entry.flags & ACE_INHERITED]
        assert acl_digest(landed) != acl_digest(before), "the inherited bit is what the digest saw"

        # The cost is reported, once per ACE, and named with both flag values: every stored ACE lost
        # `INHERITED_ACE` and kept the rest of its flags, because that bit (and only that bit) is what
        # protection takes away.
        assert len(findings) == len(before.entries), findings
        assert all(
            f"flags 0x{recorded.flags:02x}" in finding
            and f"flags 0x{recorded.flags & ~ACE_INHERITED:02x}" in finding
            for recorded, finding in zip(before.entries, findings)
        ), findings
        assert all(entry.flags & ~ACE_INHERITED == entry.flags for entry in landed.entries), (
            "no stored ACE may keep the inherited bit: "
            f"{[hex(entry.flags) for entry in landed.entries]}"
        )
        # And the same list is what `verify` returns, because apply's answer *is* a verification.
        assert verify_baseline(writable.path, before) == findings

        # Paid once: the observation that just landed is a baseline the directory matches cleanly.
        assert apply_baseline(writable.path, landed) == []
        assert verify_baseline(writable.path, landed) == []
    finally:
        restored, back = writable.put_back()

    assert _same_access(before, back), (
        f"restore did not put the access back: it said {restored}; before={before.to_document()} "
        f"after={back.to_document()}"
    )
    # The restored observation is clean the second time round: the inherited bit stayed gone, and that is
    # the only difference the round trip leaves behind.
    assert verify_baseline(writable.path, back) == []


def test_verify_names_what_differs_rather_than_returning_a_bare_boolean(writable: WriteDirectory) -> None:
    """``verify`` is a list of differences, so an answer can say *what* is wrong.

    An implementation that compared digests would return a boolean and leave the caller unable to
    explain itself. The findings come from the same ``differences`` helper the drift check uses, and the
    owner line is deliberately absent from them: ``apply_baseline`` does not write the owner, so an
    owner difference is not something this verify can ever clear.
    """

    before = writable.original
    changed = AclSnapshot(
        path=str(writable.path),
        owner="S-1-5-32-544",  # BUILTIN\Administrators, which does not match a synthetic observation
        entries=(AclEntry(0x00, 0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),),
        dacl_present=True,
    )
    try:
        assert verify_baseline(writable.path, before) == []
        findings = verify_baseline(writable.path, changed)
        assert findings, "a baseline with one entry cannot match an eleven-entry DACL"
        assert all(not finding.startswith("owner:") for finding in findings), findings
    finally:
        # `put_back` rather than an asserted-empty restore: nothing was written here, so both answers are
        # the same, and the fixture is what requires the access to be back.
        writable.put_back()


def test_apply_clears_exactly_what_the_previous_baseline_added(writable: WriteDirectory) -> None:
    """Two applies in a row: the second replaces the first rather than accumulating on top of it."""

    before = writable.original
    first = AclSnapshot(
        path=str(writable.path),
        entries=(AclEntry(0x00, 0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),),
        dacl_present=True,
    )
    second = OTHER
    try:
        apply_baseline(writable.path, first)
        apply_baseline(writable.path, second)
        landed = capture_acl(writable.path)
        assert landed.observed is True
        assert (landed.entries[0].ace_type, landed.entries[0].mask, landed.entries[0].sid) == (
            0x00,
            SYNCHRONIZE,
            USERS,
        )
        # The second DACL replaced the first outright, which is the *point* of the protection flag: with
        # the parent's inheritance left live, the first baseline's entry plus 11 re-inherited ones would
        # still sit behind this one. Nothing is here but what was asked for.
        assert [(entry.ace_type, entry.flags, entry.mask, entry.sid) for entry in landed.entries] == [
            (0x00, 0x00, SYNCHRONIZE, USERS)
        ]
    finally:
        restored, back = writable.put_back()

    assert _same_access(before, back), (
        f"restore did not put the access back: it said {restored}; before={before.to_document()} "
        f"after={back.to_document()}"
    )


# --- failure as data ------------------------------------------------------------------------------


def test_a_refused_write_is_a_finding_naming_the_call_and_the_status(writable: WriteDirectory) -> None:
    """A refusal must be reported, not raised, and it must leave the directory as it was.

    A real refusal cannot be produced on this host: ``SetSecurityInfo`` succeeds on a directory the
    current user owns, which is the whole premise of the unprotected write path. So the refusal is
    injected at this module's own seam — the private helper that performs the call — with the status a
    real refusal reports (``ERROR_ACCESS_DENIED``, 5). That substitution is the honest way to test the
    path; the alternative is a test that is green because it never reaches the code it claims to cover.

    The status is asserted by its *number* because ``_status_message`` also attaches the OS's localized
    text, which this environment renders through a non-UTF-8 console and which no test should depend on.
    """

    before = writable.original
    monkeypatch_status = 5  # ERROR_ACCESS_DENIED
    original = acl._set_security_info
    calls: list[tuple] = []

    def refuse(path, *, dacl=None, owner=None, protect_dacl=False):
        calls.append((path, dacl, owner, protect_dacl))
        return monkeypatch_status, None

    try:
        acl._set_security_info = refuse
        findings = apply_baseline(writable.path, SYNTHETIC)
    finally:
        acl._set_security_info = original

    assert calls, "the write path must have been reached"
    assert calls[0][3] is True, "the write path must ask for a protected DACL; see the module docstring"
    assert findings, "a refused write must not be reported as success"
    assert "SetSecurityInfo" in findings[0]
    assert str(monkeypatch_status) in findings[0]
    # Nothing was written, because the only thing that writes is the call that was refused.
    assert acl_digest(capture_acl(writable.path)) == acl_digest(before)
    # No `finally` restore was needed for the same reason, and the fixture is what checks it anyway.


def test_a_directory_that_is_not_there_is_a_finding_not_an_exception(tmp_path: Path) -> None:
    """A vanished directory is a fact about the world, so it comes back as data on every entry point.

    The case is real rather than theoretical: a caller holding a baseline for a data root that was moved
    or deleted gets an answer about the directory, not a traceback from inside the module. The numeric
    status is asserted because it is the half of the message a reader can look up; the OS text is
    localized and best-effort (see ``identity.py``'s ``_status_message``).
    """

    absent = tmp_path / "acl-write-absent-directory"

    observed = capture_acl(absent)
    assert observed.observed is False
    code = observed.reason.split("returned ")[1].split(" ")[0]

    findings = verify_baseline(absent, SYNTHETIC)
    assert findings == [f"capture_acl: {observed.reason}"]

    findings = apply_baseline(absent, SYNTHETIC)
    assert findings, "a write to a directory that is not there is not a successful write"
    assert "SetSecurityInfo" in findings[0]
    assert code in findings[0], findings
    assert "err=" in findings[0], findings

    # A snapshot of a directory that could not be read is not a restoration target, and "put it back"
    # has no meaning — that is the documented raise, not a finding.
    with pytest.raises(ValueError):
        restore_acl(absent, observed)


def test_an_unreadable_directory_never_verifies_as_a_match(writable: WriteDirectory) -> None:
    """The dangerous default: an empty list means "verified", so a failed read must not return one."""

    assert verify_baseline(writable.path, SYNTHETIC)  # the directory does not match, and says so
    unobserved = AclSnapshot(path=str(writable.path), reason="GetNamedSecurityInfoW returned 5")
    findings = verify_baseline(writable.path, unobserved)
    assert findings, "a baseline that was never observed cannot be verified against"
    assert "never observed" in findings[0]


def test_a_baseline_with_an_absent_dacl_is_refused_rather_than_approximated(writable: WriteDirectory) -> None:
    """``dacl_present=false`` cannot be written through this signature, so it is reported.

    A NULL DACL grants everyone full control. The one other posture this module could have substituted —
    an empty-but-present ACL — denies everyone, is refused a few tests below for being un-undoable, and
    is the opposite posture anyway. Substituting either one for the other would be a silent permission
    change, and the test asserts the refusal instead.
    """

    before = writable.original
    absent = AclSnapshot(path=str(writable.path), entries=(), dacl_present=False)
    try:
        findings = apply_baseline(writable.path, absent)
        assert findings, "silently writing a different posture must not be possible"
        assert "absent DACL" in findings[0]
        assert acl_digest(capture_acl(writable.path)) == acl_digest(before), "nothing may be written"
    finally:
        writable.put_back()


def test_an_empty_baseline_is_refused_because_it_cannot_be_undone(writable: WriteDirectory) -> None:
    """An explicit empty DACL *is* a real instruction ("deny everyone") — and this module refuses it.

    The refusal is not "Windows cannot": the write lands, and this test's predecessor asserted that it
    did. It is that the landing is **final**. An empty DACL denies every principal, the caller included,
    so the directory that just received it cannot be opened again for ``WRITE_DAC``: measured on this
    host, ``CreateFileW`` returns ``ERROR_ACCESS_DENIED``(5) afterwards and ``restore_acl`` is refused
    with it — under an *elevated* token, which is why the escape route is ownership plus a privilege
    this build does not have. Writing it would make one unprivileged library call a door that closes
    behind the caller, and ``restore_acl`` exists to promise that applying a baseline is not that door.

    So the answer is a finding, the same shape as the absent-DACL refusal above: a stable fact about
    what this module will do, that no retry would change, rather than the ``ValueError`` reserved for a
    caller who handed in nonsense. What is asserted is the whole decision — the refusal names the
    reason, nothing is written, and the directory is still restorable afterwards, which is the half
    that would be false if this test wrote the empty DACL instead.
    """

    before = writable.original
    empty = AclSnapshot(path=str(writable.path), entries=(), dacl_present=True)
    findings = apply_baseline(writable.path, empty)

    assert findings, "an empty baseline must not be reported as applied"
    assert "empty DACL" in findings[0], findings
    assert "restore_acl" in findings[0], "the finding must say what makes it un-undoable"
    assert "ERROR_ACCESS_DENIED(5)" in findings[0], findings

    # Nothing was written: the directory still has the DACL it had, and it is still openable for a write,
    # which is exactly the property the refusal protects.
    landed = capture_acl(writable.path)
    assert landed.observed is True
    assert landed.dacl_present is True
    assert acl_digest(landed) == acl_digest(before)

    findings, back = writable.put_back()
    assert verify_baseline(writable.path, back) == []
    assert _same_access(before, back), (
        f"the refused write must leave the directory restorable: it said {findings}; "
        f"before={before.to_document()} after={back.to_document()}"
    )


def test_a_deny_ace_is_written_and_is_read_back_as_the_change_it_is(writable: WriteDirectory) -> None:
    """A deny ACE is enforceable, and the reader sees it in the order that makes it bite.

    This is the one test that uses a deny, and it names ``BUILTIN\\Backup Operators`` rather than
    ``BUILTIN\\Users``. That is not an arbitrary well-known SID. A deny that matches the token running
    the tests takes the directory away from the process that wrote it, and this test — unlike its
    predecessor — puts the directory back. Measured on this host, with a protected DACL whose only entry
    was that deny, ``CreateFileW(path, WRITE_DAC)`` was refused with ``ERROR_ACCESS_DENIED``(5)
    afterwards; a deny that matches no group in the token does not refuse it, and the stored DACL is the
    same deny. That is also why the earlier version of this test could not restore anything: it denied
    ``BUILTIN\\Users``, and the process is in that group.

    The allow for ``Everyone`` is part of the measurement, not padding: a protected DACL needs at least
    one allow that matches the token for the caller to keep ``WRITE_DAC`` on the directory it just wrote
    (measured: an allow for ``Everyone``, ``BUILTIN\\Users`` or ``NT AUTHORITY\\Authenticated Users``
    was enough; a DACL with no matching allow was not). A test that wants to restore what it wrote has to
    stay in that set, and saying so is more useful to a reader than a silently chosen SID.

    What is asserted is the half a permission boundary needs: the deny is written, stored as a deny, and
    read back **first**, ahead of the allow, with the flags Windows uses for it. The refusal half of "the
    directory is now denied" is not asserted here because it is a fact about *this machine's* group
    membership rather than about the write — that a DACL with no matching allow refuses the caller is
    what :func:`test_an_empty_baseline_is_refused_because_it_cannot_be_undone` pins, with the same
    measured status.
    """

    before = writable.original
    denying = snapshot(
        [
            (0x01, 0x00, SYNCHRONIZE, BACKUP_OPERATORS),
            (0x00, 0x00, FILE_ALL_ACCESS, "S-1-1-0"),  # Everyone, so this test can undo its own write
        ]
    )
    try:
        assert apply_baseline(writable.path, denying) == [], "this deny matches nothing in this token"

        landed = capture_acl(writable.path)
        assert landed.observed is True
        assert landed.entries, "the deny must have landed"
        assert landed.entries[0].ace_type == 0x01, "the deny must be stored as a deny"
        assert landed.entries[0].flags == 0x00, "and not be laundered into an inherited ACE"
        assert landed.entries[0].mask == SYNCHRONIZE
        assert landed.entries[0].sid == BACKUP_OPERATORS
        # The order is the baseline's order — the deny ahead of the allow — and the whole DACL is those
        # two entries: protection means none of the parent's 11 ACEs is left to re-grant what the deny
        # withholds.
        assert [(entry.ace_type, entry.sid) for entry in landed.entries] == [
            (0x01, BACKUP_OPERATORS),
            (0x00, "S-1-1-0"),
        ], landed.to_document()
        # And the directory does not match the original any more, which is what a deny is for.
        assert verify_baseline(writable.path, before)
    finally:
        restored, back = writable.put_back()

    assert _same_access(before, back), (
        f"restore did not put the access back: it said {restored}; before={before.to_document()} "
        f"after={back.to_document()}"
    )


# --- the deliberate raise -------------------------------------------------------------------------


def test_a_caller_passing_nonsense_gets_a_value_error(tmp_path: Path) -> None:
    """Where a raise is the right answer, and only there: no privilege or retry would help.

    Four shapes are pinned. Each is a *caller* error rather than a machine fact, so returning it as a
    finding would invite a caller to retry something that can never succeed. Everything else in this
    module is data.
    """

    target = tmp_path / "acl-write-nonsense"
    target.mkdir()

    unsupported = AclSnapshot(
        path=str(target),
        entries=(AclEntry(0x05, 0x00, 0x00, USERS),),  # an ACE type this module cannot write
        dacl_present=True,
    )
    with pytest.raises(ValueError) as refused:
        apply_baseline(target, unsupported)
    assert "ACE type 0x05" in str(refused.value)

    trustee_less = AclSnapshot(
        path=str(target),
        entries=(AclEntry(0x00, 0x00, FILE_ALL_ACCESS, None),),
        dacl_present=True,
    )
    with pytest.raises(ValueError) as refused:
        apply_baseline(target, trustee_less)
    assert "no trustee SID" in str(refused.value)

    not_a_sid = AclSnapshot(
        path=str(target),
        entries=(AclEntry(0x00, 0x00, FILE_ALL_ACCESS, "not-a-sid"),),
        dacl_present=True,
    )
    with pytest.raises(ValueError) as refused:
        apply_baseline(target, not_a_sid)
    assert "ConvertStringSidToSidW" in str(refused.value)

    unobserved = AclSnapshot(path=str(target), reason="GetNamedSecurityInfoW returned 5")
    with pytest.raises(ValueError):
        apply_baseline(target, unobserved)
    with pytest.raises(ValueError):
        restore_acl(target, unobserved)

    # An unsupported ACE must be refused before anything is written, so this directory is untouched.
    assert verify_baseline(target, unsupported) != []


def test_the_ace_flag_constants_the_tests_rely_on_are_the_windows_ones() -> None:
    """The tests name ACE flags as numbers; pinning them keeps the assertions from being self-referential.

    ``ACE_INHERITED`` is the bit the tests use to tell "the kernel added this" from "we asked for this",
    and it is the load-bearing distinction in the inheritance test above. A wrong value there would make
    that test pass for the wrong reason.
    """

    assert ACE_INHERITED == 0x10
    assert ACE_CONTAINER_INHERIT == 0x02
    assert ACE_OBJECT_INHERIT == 0x01
    assert acl._ACE_ACCESS_ALLOWED == 0x00
    assert acl._ACE_ACCESS_DENIED == 0x01
    assert acl.SE_FILE_OBJECT == 1
    assert acl.DACL_SECURITY_INFORMATION == 0x00000004
    assert acl.OWNER_SECURITY_INFORMATION == 0x00000001


def test_the_write_path_reaches_no_cli_surface() -> None:
    """Without the broker there is no approved caller, so this must stay reachable only as a library.

    Asserted in both directions: the API exists on the module, and the CLI module neither imports these
    names nor names them anywhere. A later stage that wires them up will have to delete this test, which
    is the point — the wiring should be a decision someone makes, not something that slips in.
    """

    for name in ("acl_baseline", "apply_baseline", "verify_baseline", "restore_acl"):
        assert hasattr(acl, name), name
        assert name in acl.__all__ or name == "acl_baseline", name

    from airoot import cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    for name in ("apply_baseline", "verify_baseline", "restore_acl", "SetSecurityInfo"):
        assert name not in source, f"cli.py must not reach the write side ({name})"

    # And the module says so where a reader will look first.
    docstring = acl.__doc__ or ""
    assert "not protected" in docstring
    assert "SameSecurityInfo" not in docstring  # a typo guard for the name itself
    assert "SetSecurityInfo" in docstring


def test_the_write_side_is_exercised_at_all(tmp_path: Path) -> None:
    """A guard against the whole file going vacuous: the imports and the seam must be the real ones.

    If a refactor renamed the private helpers, every write-path test above would fail — but if someone
    made the write path a stub that returns successes, the round-trip test would still pass. This pins
    the two pieces of machinery that make the round trip real: the ACL builder produces a non-empty
    buffer for a non-empty entry list, and the pointer handed to the kernel is a real address.
    """

    entries = (AclEntry(0x00, 0x00, FILE_ALL_ACCESS, AUTHENTICATED_USERS),)
    buffer, status, problem = acl._build_acl(entries)
    assert problem is None and status == 0
    assert buffer is not None
    assert len(bytes(buffer)) >= ctypes.sizeof(acl._Acl) + ctypes.sizeof(acl._AccessAce)
    # The header is written by `InitializeAcl` and the ACE by `AddAccessAllowedAceEx`; the ACL's own
    # `AclSize`/`AceCount` fields are how the kernel reads it back, so they are worth reading here.
    raw = bytes(buffer)
    assert int.from_bytes(raw[4:6], "little") == 1, "the ACL must report one ACE"
    assert raw[0] == acl._ACL_REVISION

    descriptor, pointer, problem = acl._dacl_pointer(buffer)
    assert problem is None
    assert pointer is not None and pointer.value
    # `descriptor` must be kept alive by the caller: the pointer is an offset inside it.
    assert ctypes.string_at(descriptor) is not None
