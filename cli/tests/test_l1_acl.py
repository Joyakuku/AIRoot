"""L1: the read-only ACL observation behind ``DATA_ROOT_ACL_DRIFT`` (ADR-0023).

``doctor`` has to be able to say whether a data root's ACL drifted, and the observation behind that
has to work in this project's shape: no elevation, no new dependency (``jsonschema`` stays the only
runtime one), and failure as data.

Two properties are asserted here rather than described, because both are easy to get wrong in a way
nothing else would notice:

* **order is preserved** — Windows ACE order carries meaning, so a reordering must move the digest.
  An implementation that sorted for stability would look tidier and would launder a real permission
  change into "unchanged".
* **an unreadable ACL is not an empty one** — the stored document has to carry the difference,
  otherwise a failed observation reads back as "this directory grants nothing", which is the most
  dangerous possible misreading of this data.
"""

from __future__ import annotations

from airoot.caps.acl import AclEntry, AclSnapshot, acl_digest, capture_acl, differences


def snapshot(entries: list[tuple[int, int, int, str]], owner: str = "S-1-5-18") -> AclSnapshot:
    return AclSnapshot(
        path="X:/synthetic",
        owner=owner,
        entries=tuple(AclEntry(*item) for item in entries),
        dacl_present=True,
    )


def test_the_digest_moves_when_aces_are_reordered() -> None:
    """A reordering is a change, not noise: deny-before-allow is not the same as allow-before-deny.

    This is the assertion behind the "we do not sort" decision. Without it, "ACE order is preserved"
    would be a comment, and a future tidy-up that sorted the entries would pass every other test in
    the suite.
    """

    first = (0x00, 0x00, 0x001F01FF, "S-1-5-32-544")
    second = (0x01, 0x00, 0x00100000, "S-1-5-32-545")

    allow_then_deny = snapshot([first, second])
    deny_then_allow = snapshot([second, first])

    assert acl_digest(allow_then_deny) != acl_digest(deny_then_allow)
    # Same entries, same order -> same digest, so the digest is a function of the observation.
    assert acl_digest(allow_then_deny) == acl_digest(snapshot([first, second]))
    # And the difference is nameable, which is what an answer needs in order to be actionable.
    assert differences(allow_then_deny, deny_then_allow)
    assert differences(allow_then_deny, allow_then_deny) == []


def test_a_failed_observation_is_data_and_never_reads_back_as_an_empty_acl() -> None:
    """Failure is data: a reason, no entries, and a document that says which of the two it was."""

    missing = capture_acl("X:/definitely-not-a-directory-airoot")

    assert missing.observed is False
    assert missing.reason, "an unreadable ACL must come with a reason, not silence"
    assert missing.entries == ()
    assert missing.dacl_present is False

    # The load-bearing part: the *stored* form must distinguish "could not read" from "grants
    # nothing". If it did not, a registry baseline recorded from a failed read would later compare
    # unequal to a real ACL and report drift against a directory that never changed.
    document = missing.to_document()
    assert document["observed"] is False
    assert document["reason"] == missing.reason
    assert document["digest"] is None

    restored = AclSnapshot.from_document(missing.path, document)
    assert restored.observed is False
    assert restored.reason == missing.reason

    # An observed-but-empty DACL is the other case, and it is *not* the same fact.
    empty = AclSnapshot(path="X:/synthetic", owner="S-1-5-18", entries=(), dacl_present=True)
    assert empty.observed is True
    assert empty.to_document()["observed"] is True
    assert empty.to_document()["digest"] is not None

    # Two observations where one side failed are not comparable, and the helper says so by staying
    # out of the way: the caller must consult `observed`, which the doctor check does.
    assert differences(empty, missing) == []
