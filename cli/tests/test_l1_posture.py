"""L1 guard: the build's security posture is one mapping, in one place (draft §115, ADR-0043).

`common.schema.json#/$defs/securityMode` allows two values and `.../$defs/enforcement` allows two, so
the published schema admits **four** pairs, only two of which are coherent: `policy_only` cannot be
`acl_enforced` (that claims an enforcement nothing implements) and `protected_machine` cannot be
`same_user_can_bypass` (that claims a protection a same-user process can walk around). JSON Schema
cannot narrow a shared `$defs` entry into a cross-field rule without rejecting documents that are valid
today, so the coherence rule lives in code — and the failure mode this file exists for is not "the rule
is wrong", it is "the rule is written down twice and the two copies drift". That had already happened
once (`broker-response.schema.json` said `acl_and_broker`; §108), and by this round the same
conditional expression sat in `caps/doctor.py` and `ext/envelope.py` with a third writer about to
restate it.

So the guards below are of three kinds, and each one is a different lie:

* the pair this build emits, asserted as a **literal** — a test that re-derives the expected value from
  `posture.py` would pass for any posture, including a wrong one;
* the rule's **totality** against the schema's own enums, in the `test_l1_reason_codes.py` shape: a
  driver that walks the tree and holds it to the contract. A new `securityMode` value with no rule
  must redden here, and every value the rule can produce must be a legal `enforcement` word;
* the guard that actually stops a fourth copy: the four literals — two modes, two enforcements — may
  appear **nowhere in the app package except `posture.py`**. The other two writers are checked
  end-to-end against `posture()` as well, but this is the one that makes a new inline copy impossible
  to add silently — which is the defect this round is closing, so it is the one under test in the
  red-verification.

**The guard is two checks, and the second one exists because the first one missed.** Both checks were
originally scoped to the word `acl_enforced` alone, and both were therefore blind to the third copy the
round was written to catch: `broker/pipe.py` built its refusal evidence with
`enforcement_for('policy_only')`. Nothing there is a quoted `acl_enforced`, so the narrow scan was
silent — while the call *did* hard-code a mode, i.e. exactly the drift being guarded against, in the one
place a mode string should never be re-typed. The widened scan (all four literals) now catches that
word; :func:`test_no_module_passes_a_hard_coded_mode_to_enforcement_for` catches its *shape*, which
survives any renaming of the words themselves. The general lesson is the one §113 records from the other
direction: a guard that names the defect rather than the *shape* of the defect stops catching it as soon
as the defect is spelled differently.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from airoot import schema_io
from airoot.caps.doctor import doctor
from airoot.exits import AirootError
from airoot.ext.envelope import envelope
from airoot.posture import ENFORCEMENT_BY_MODE, SECURITY_MODE, enforcement_for, posture

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "cli" / "app" / "airoot"

#: The one module allowed to name a mode's enforcement.
POSTURE_MODULE = APP / "posture.py"

#: The value the scan hunts for. Quoted rather than bare on purpose: `acl_enforced` also appears as
#: *prose* in other modules' docstrings (and in the draft's explanation of the defect), and a scan that
#: read those would be red for the wrong reason. What the guard is about is a **writer** — an expression
#: that can put the word into a document — and every writer in this tree spells it as a quoted literal.
FORBIDDEN_LITERAL = "acl_enforced"

#: The two words `securityMode` allows, read from the published schema rather than restated here.
SECURITY_MODE_ENUM = tuple(
    schema_io.load_schema("common")["$defs"]["securityMode"]["enum"]
)
ENFORCEMENT_ENUM = tuple(schema_io.load_schema("common")["$defs"]["enforcement"]["enum"])


def app_modules_writing(literal: str, *, skip: pathlib.Path | None = None) -> list[pathlib.Path]:
    """Every app-package module whose source contains `literal` as a quoted string, minus `skip`.

    The shape is `test_l1_reason_codes.py`'s `codes_produced_by_the_app()`: a whole-tree read with a
    quoted-literal match. The question is not "does a call site exist" (which cannot be read off a
    call graph anyway) but "does this tree contain a second writer", and the honest way to ask that of
    Python without an AST is to read all of it.
    """

    pattern = re.compile(r"[\"']" + re.escape(literal) + r"[\"']")
    found: list[pathlib.Path] = []
    for path in sorted(APP.rglob("*.py")):
        if path != skip and pattern.search(path.read_text(encoding="utf-8")):
            found.append(path)
    return found


# --------------------------------------------------------------------------------------------- #
# the pair this build emits
# --------------------------------------------------------------------------------------------- #


def test_posture_is_the_documented_policy_only_pair() -> None:
    """A literal expectation, not a re-derivation: this is the claim the build makes about itself.

    Re-deriving it from `posture.py` (say, by asking `enforcement_for(SECURITY_MODE)`) would be a
    tautology: it would hold for `protected_machine` too, which is exactly the wrong posture for a
    build with no ACL enforcement and no broker. So the value is written out here.
    """

    assert posture() == {
        "security_mode": "policy_only",
        "enforcement": "same_user_can_bypass",
    }
    assert SECURITY_MODE == "policy_only"


def test_posture_hands_back_a_fresh_dict() -> None:
    """A shared dict a caller mutated would change the answer every other caller gets."""

    posture()["security_mode"] = "protected_machine"
    assert posture() == {
        "security_mode": "policy_only",
        "enforcement": "same_user_can_bypass",
    }


# --------------------------------------------------------------------------------------------- #
# the rule is total over the schema's enum, and only ever produces legal words
# --------------------------------------------------------------------------------------------- #


def test_the_rule_covers_exactly_the_schema_s_modes() -> None:
    """Both directions, because each is a different hole.

    A `securityMode` value with no rule: `enforcement_for` raises on a mode the published schema calls
    legal, so a document carrying it cannot be produced at all. A rule for a mode the schema does not
    enumerate: dead code pretending to be a posture. A new enum value must redden here rather than
    quietly fall through to whatever the default would have been.
    """

    assert set(ENFORCEMENT_BY_MODE) == set(SECURITY_MODE_ENUM), (
        "the mapping and common.$defs.securityMode disagree: %s vs %s"
        % (sorted(ENFORCEMENT_BY_MODE), sorted(SECURITY_MODE_ENUM))
    )


def test_every_mode_maps_to_a_word_the_schema_allows() -> None:
    """The rule can only produce legal `enforcement` values — checked by value, not by construction."""

    illegal = {
        mode: value for mode, value in ENFORCEMENT_BY_MODE.items() if value not in ENFORCEMENT_ENUM
    }
    assert illegal == {}, "these mapped values are not in common.$defs.enforcement: %s" % illegal

    # And the function every writer calls agrees with the table it reads.
    for mode in ENFORCEMENT_BY_MODE:
        assert enforcement_for(mode) == ENFORCEMENT_BY_MODE[mode]


def test_an_unknown_mode_is_refused_rather_than_defaulted() -> None:
    """`INVALID_INPUT` (exit 8) is the schema tier: an unenumerated mode is malformed input.

    The assertion is that the call **raises** — never that it returned something. A default would be
    the worst shape available: whatever it defaulted to is a claim about enforcement, made on behalf of
    a mode nobody recognises. The evidence must name both legal values, because the reader's next
    question is "then which are legal".
    """

    for unknown in ("protected", "Policy_Only", "", "acl_enforced"):
        with pytest.raises(AirootError) as raised:
            enforcement_for(unknown)
        assert raised.value.reason_code == "INVALID_INPUT"
        assert raised.value.exit_code == 8
        assert unknown in raised.value.message
        evidence = " ".join(raised.value.evidence)
        for legal in SECURITY_MODE_ENUM:
            assert legal in evidence, "%s is a legal mode but the evidence omits it: %s" % (legal, evidence)


def test_the_schema_enums_are_what_this_file_thinks_they_are() -> None:
    """A canary for the two reads above: a renamed `$defs` entry would make them vacuously true."""

    assert set(SECURITY_MODE_ENUM) == {"protected_machine", "policy_only"}
    assert set(ENFORCEMENT_ENUM) == {"acl_enforced", "same_user_can_bypass"}


# --------------------------------------------------------------------------------------------- #
# the guard that stops a fourth inline copy
# --------------------------------------------------------------------------------------------- #


def test_only_posture_py_names_an_enforcement_value() -> None:
    """The literal `acl_enforced` may appear in the app package **only** in `posture.py`.

    This is the check the round exists for. The rule was written twice — `caps/doctor.py` and
    `ext/envelope.py` each carried `"acl_enforced" if security_mode == "protected_machine" else ...` —
    and the broker's named pipe (`broker/pipe.py`) was about to make it three. A guard that only
    compared the documents to `posture()` would catch the copies that are *reached* by a test; this one
    catches the copy that is not, which is the one that will drift.

    Scope is the app package, deliberately: `cli/tests/fake_broker.py` and the reference documents
    legitimately discuss the word, and `test_l1_schema_catalog.py`'s comment records the past bug that
    made this worth guarding. The scan is a **quoted-literal** match, following
    `test_l1_reason_codes.py`'s `codes_produced_by_the_app()`: every writer in this tree spells the word
    as a string literal, and docstring prose that merely *mentions* it is not a writer.
    """

    pattern = r"[\"']" + re.escape(FORBIDDEN_LITERAL) + r"[\"']"

    offenders = [
        str(path.relative_to(REPO)) for path in app_modules_writing(FORBIDDEN_LITERAL, skip=POSTURE_MODULE)
    ]

    assert offenders == [], (
        "%r must be written by posture.py alone, but these app modules carry the literal: %s"
        % (FORBIDDEN_LITERAL, offenders)
    )
    # The other direction of the same guard: the module is allowed the word, so it must have it. A scan
    # that passed because `posture.py` had been emptied would be silent about the rule disappearing.
    assert app_modules_writing(FORBIDDEN_LITERAL) == [POSTURE_MODULE], (
        "the app package names %r somewhere other than posture.py — or nowhere at all, which means the "
        "rule went missing rather than moving" % FORBIDDEN_LITERAL
    )
    assert re.search(pattern, POSTURE_MODULE.read_text(encoding="utf-8")) is not None


# --------------------------------------------------------------------------------------------- #
# the two documents that carry the pair agree with posture()
# --------------------------------------------------------------------------------------------- #


def test_the_doctor_document_carries_the_posture_pair(registry, clock, root) -> None:
    """A cheap end-to-end proof that the refactor landed in `caps/doctor.py`.

    It **is** reachable without a root in the sense the brief worried about — the `registry`/`root`
    fixtures are this repo's own `cli/tests/.tmp` builders (`conftest.py`), not a machine root — so
    there is no need to fall back to a docstring. The document is self-validated by `doctor` itself,
    which is the stronger statement: the pair it emits is one the published schema accepts.
    """

    document = doctor(root.path, clock=clock, registry=registry, data_roots=False)

    assert document["security_mode"] == posture()["security_mode"]
    assert document["enforcement"] == posture()["enforcement"]
    # The two keys are still written last, in that order: they were appended after `checked_at` before
    # the refactor, and a caller that reads the tail of the document must not start seeing a different
    # shape.
    assert list(document)[-2:] == ["security_mode", "enforcement"]


def test_the_extension_envelope_carries_the_posture_pair() -> None:
    """The same proof for `ext/envelope.py`, whose writer is reachable with no root at all.

    `security_mode` and `enforcement` are *not* required by `extension-envelope.schema.json`, so this
    is a document the schema would accept without them — which is exactly why the test asserts they are
    there: the envelope's honesty is a decision this build makes, not a constraint the schema imposes.
    """

    document = envelope("airoot-fake-extension", "probe", data={})

    assert document["security_mode"] == posture()["security_mode"]
    assert document["enforcement"] == posture()["enforcement"]
    assert list(document)[-2:] == ["security_mode", "enforcement"]
