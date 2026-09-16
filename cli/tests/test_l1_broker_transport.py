"""L1: the broker's framing layer — one request is one frame, and a bad frame never allocates.

`docs/broker` §3 is the authority: *一次提交请求必须是一个完整 envelope，不能由客户端分段拼接安全字段*.
These tests pin the wire that rule implies, and nothing about a service: no pipe is opened, no process
is launched, no file is touched. Every input here is bytes in memory and every `read` is a callable this
file owns, so the whole suite is about which bytes come out and which refusal comes back.

What is asserted, and where the authority for each is:

* the **layout** — a hand-written 4-byte little-endian uint32 length and the exact payload bytes, because
  a round-trip through this module's own encoder would prove only that it agrees with itself;
* **one envelope, never a segmented one** — the reader returns a whole payload or refuses, and never a
  partial conversation;
* **refuse before allocating** — an oversized declaration is refused with the body never requested, which
  is asserted on a recorder's list of requested sizes rather than on a memory measurement;
* **determinism** — the canonical byte form is stable across runs and independent of key order, while the
  reader must not depend on key order at all (the docstring's claim, pinned in both directions);
* **every rule** — `transport.FRAME_RULES` is the rule set as data, and `test_every_frame_rule_is_reachable`
  proves each token is reachable, so a refusal added without a test cannot hide behind a green suite.
"""

from __future__ import annotations

import ast
import json
import pathlib
import struct
from collections.abc import Callable

import pytest

from airoot.broker import protocol, transport
from airoot.exits import EXIT_INVALID_INPUT, AirootError

MAX = transport.MAX_FRAME_BYTES


def header(length: int) -> bytes:
    """The four length bytes for ``length``, spelled the way the contract says: little-endian uint32.

    Written with the format string rather than through the module's own `encode_frame`, so the tests
    about a *declared* length can describe lengths no document could legitimately reach.
    """

    return struct.pack("<I", length)


class Recorder:
    """A byte source answering ``read(n)`` with **at most** ``n`` bytes, recording every request.

    ``requests`` is the reader's half of the conversation, so "did it check the bound before asking for
    the body" and "did it loop when a read came back short" are assertions about a list rather than
    about timing or memory. ``chunk`` forces a short read; ``over`` makes the callable hand back more
    than it was asked for, which is a broken `read` rather than a frame.
    """

    def __init__(self, data: bytes = b"", *, chunk: int | None = None, over: int = 0) -> None:
        self.data = data
        self.chunk = chunk
        self.over = over
        self.requests: list[int] = []

    def __call__(self, n: int) -> bytes:
        self.requests.append(n)
        want = n + self.over
        if self.chunk is not None:
            want = min(want, self.chunk)
        out, self.data = self.data[:want], self.data[want:]
        return out


#: A hand-checkable document: two keys written out of order, and one non-ASCII value, so the exact bytes
#: below pin the sort order, the minimal separators and UTF-8 rather than ``\\u00e9`` all at once.
SMALL = {"b": "é", "a": 1}
SMALL_PAYLOAD = b'{"a":1,"b":"\xc3\xa9"}'
#: 15 characters, 16 UTF-8 bytes: `é` is two of them, which is what pins `ensure_ascii=False`.
SMALL_FRAME = b"\x10\x00\x00\x00" + SMALL_PAYLOAD


def document_of_payload_size(size: int) -> dict:
    """A document whose canonical payload is exactly ``size`` bytes.

    The JSON overhead is measured from the encoder itself instead of being re-spelled here, because the
    point of the two bound tests is the *bound*, and a second copy of the canonical form inside the test
    would redden on the wrong change.
    """

    overhead = len(transport.encode_frame({"k": ""})) - transport.FRAME_HEADER_BYTES
    return {"k": "x" * (size - overhead)}


def assert_refusal(error: AirootError, rule: str, numbers: dict[str, int]) -> None:
    """The contract every refusal in this layer keeps, whatever the rule."""

    assert error.reason_code == "INVALID_INPUT"
    assert error.exit_code == EXIT_INVALID_INPUT, "a malformed frame is the caller's input (exit 8)"
    assert error.details.get("rule") == rule, f"the refusal must name its rule, not just its wording: {error.evidence}"
    assert f"rule: {rule}" in error.evidence
    assert error.message in error.evidence, "the sentence has to be in the evidence, not only in the message"
    for name, value in numbers.items():
        assert error.details.get(name) == value, f"{name}: expected {value}, got {error.details.get(name)}"


# --------------------------------------------------------------------------------------------- #
# the contract as data: the header, the bound, and the rule set
# --------------------------------------------------------------------------------------------- #


def test_the_header_is_a_four_byte_little_endian_uint32() -> None:
    assert transport.FRAME_HEADER_BYTES == 4
    assert struct.calcsize(transport.LENGTH_FORMAT) == transport.FRAME_HEADER_BYTES, (
        "the format string and the byte count are two spellings of one fact; they must agree"
    )
    assert transport.LENGTH_FORMAT == "<I"
    # Little-endian, spelled out: 1 is `01 00 00 00`, and the high byte is the fourth one.
    assert header(1) == b"\x01\x00\x00\x00"
    assert header(0x01020304) == b"\x04\x03\x02\x01"


def test_the_bound_is_a_real_bound_on_a_real_document() -> None:
    """A `broker-request` is small; the bound has to be justified by that, not chosen for roundness."""

    assert transport.MAX_FRAME_BYTES > 0
    assert transport.MAX_FRAME_BYTES == 1 << (transport.MAX_FRAME_BYTES.bit_length() - 1), (
        "a power of two, so the bound reads as a width rather than as a tuned threshold"
    )

    # The largest document this schema can carry, built by the layer that owns the shape: a `commit_plan`
    # request whose ids are at the schema's own 128-character ceiling (`common.$defs.id`) and whose two
    # references are as long as a Windows path can be usefully be. It must sit far under the bound, or
    # the bound would refuse requests the contract calls valid.
    long_id = "a" * 128
    request = protocol.build_request(
        operation="commit_plan",
        request_id=long_id,
        plan_ref="state/plans/" + "p" * 200 + ".json",
        approval_ref="state/approvals/" + "q" * 200 + ".json",
        client={"sid": "S-1-5-21-1000", "pid": 4242, "integrity": "medium", "application_id": long_id},
    )
    frame = transport.encode_frame(request)
    assert len(frame) < MAX // 64, (
        f"the largest realistic request frames to {len(frame)} bytes against a {MAX}-byte bound; "
        "the bound is not derived from a real document"
    )


def test_the_rule_set_is_data_and_has_no_duplicates() -> None:
    assert len(set(transport.FRAME_RULES)) == len(transport.FRAME_RULES)
    assert all(rule and rule.islower() for rule in transport.FRAME_RULES)


# --------------------------------------------------------------------------------------------- #
# the exact layout, hand-written
# --------------------------------------------------------------------------------------------- #


def test_the_exact_byte_layout_of_a_small_document() -> None:
    """Bytes written by hand: the header, then the payload — never a round-trip through the decoder."""

    assert transport.encode_frame(SMALL) == SMALL_FRAME
    # Spelled out one more time, as the four pieces a reader sees when it looks at the wire.
    assert SMALL_FRAME[:4] == b"\x10\x00\x00\x00", "16 bytes, little-endian"
    assert SMALL_FRAME[4:] == b'{"a":1,"b":"\xc3\xa9"}', "sorted keys, minimal separators, UTF-8"
    assert len(SMALL_PAYLOAD) == 16
    # `ensure_ascii=False` is the difference between 16 bytes and 22, so pin that it is not escaping.
    assert b"\\u00e9" not in SMALL_FRAME


def test_a_real_broker_request_frames_to_its_canonical_bytes() -> None:
    """The document the broker will actually receive, byte-stable from one build to the next."""

    request = protocol.build_request(
        operation="commit_plan",
        request_id="req/commit/0001",
        plan_ref="state/plans/plan-0001.json",
        approval_ref="state/approvals/approval-0001.json",
        client={"sid": "S-1-5-21-1000", "pid": 4242, "integrity": "medium", "application_id": "airoot-cli"},
    )
    expected = (
        b'{"approval_ref":"state/approvals/approval-0001.json",'
        b'"client":{"application_id":"airoot-cli","integrity":"medium","pid":4242,'
        b'"sid":"S-1-5-21-1000"},'
        b'"operation":"commit_plan",'
        b'"plan_ref":"state/plans/plan-0001.json",'
        b'"protocol_version":1,'
        b'"request_id":"req/commit/0001"}'
    )
    frame = transport.encode_frame(request)
    assert frame[:4] == header(len(expected))
    assert frame[4:] == expected


# --------------------------------------------------------------------------------------------- #
# round-trip and determinism
# --------------------------------------------------------------------------------------------- #


def test_a_frame_round_trips_through_the_reader() -> None:
    frame = transport.encode_frame(SMALL)
    read = Recorder(frame)
    payload = transport.read_frame(read)
    assert payload == SMALL_PAYLOAD
    assert transport.decode_frame(payload) == SMALL
    assert read.requests == [transport.FRAME_HEADER_BYTES, len(SMALL_PAYLOAD)]


def test_the_same_document_encodes_to_the_same_bytes_twice() -> None:
    assert transport.encode_frame(SMALL) == transport.encode_frame(dict(SMALL))


def test_two_key_orders_encode_to_the_same_bytes() -> None:
    """Including nested objects: `sort_keys` is recursive, and a nested dict is where that shows."""

    left = {"b": {"y": 2, "x": 1}, "a": [1, {"d": 4, "c": 3}]}
    right = {"a": [1, {"c": 3, "d": 4}], "b": {"x": 1, "y": 2}}
    assert transport.encode_frame(left) == transport.encode_frame(right)
    # And the sorted form is the readable one, not an accident of insertion order.
    assert transport.encode_frame(left)[4:] == b'{"a":[1,{"c":3,"d":4}],"b":{"x":1,"y":2}}'


def test_the_reader_does_not_depend_on_the_key_order_the_sender_chose() -> None:
    """The canonical form is the sender's courtesy; a peer will not preserve our key order."""

    peer_payload = b'{"b":"\xc3\xa9","a":1}'  # the same document, keys the other way round
    frame = header(len(peer_payload)) + peer_payload
    assert transport.decode_frame(transport.read_frame(Recorder(frame))) == SMALL
    assert peer_payload != SMALL_PAYLOAD


def test_the_frame_layer_does_not_validate_the_schema() -> None:
    """Framing answers "were these bytes a document"; "is it a `broker-request`" is protocol's question."""

    payload = b'{"anything":[1],"not_a_broker_field":true}'
    assert transport.decode_frame(payload) == {"anything": [1], "not_a_broker_field": True}


# --------------------------------------------------------------------------------------------- #
# EOF, short reads, and a whole payload every time
# --------------------------------------------------------------------------------------------- #


def test_a_clean_eof_before_any_header_byte_is_none() -> None:
    """A client that connected and said nothing is not an error; it is an ordinary disconnect."""

    read = Recorder(b"")
    assert transport.read_frame(read) is None
    assert read.requests == [transport.FRAME_HEADER_BYTES], "one attempt, then the stream says so"


def test_a_header_split_across_two_reads_is_read() -> None:
    """`read(n)` returns *at most* `n`: a reader that trusts one call works until a peer writes in pieces."""

    read = Recorder(SMALL_FRAME, chunk=2)
    assert transport.read_frame(read) == SMALL_PAYLOAD
    assert read.requests[:2] == [transport.FRAME_HEADER_BYTES, 2], (
        "the header arrived as 2 + 2 bytes, so the reader had to loop for the second half"
    )


def test_a_body_delivered_one_byte_at_a_time_is_read() -> None:
    read = Recorder(SMALL_FRAME, chunk=1)
    assert transport.read_frame(read) == SMALL_PAYLOAD
    # Four calls to assemble the header (asking for what is left each time, 4 -> 3 -> 2 -> 1), then one
    # per payload byte: a reader that trusted a single call would have stopped at the first byte.
    assert read.requests[:4] == [4, 3, 2, 1]
    assert len(read.requests) == transport.FRAME_HEADER_BYTES + len(SMALL_PAYLOAD)


def test_the_body_is_read_as_one_request_for_the_declared_length() -> None:
    frame = transport.encode_frame({"k": "v" * 500})
    read = Recorder(frame)
    assert transport.read_frame(read) == frame[4:]
    assert read.requests == [transport.FRAME_HEADER_BYTES, len(frame) - 4]


def test_a_truncated_body_never_comes_back_as_a_shorter_payload() -> None:
    """The rule §3 exists for: no caller may ever observe a partially-built conversation."""

    read = Recorder(header(4096) + b"x" * 100)
    with pytest.raises(AirootError) as error:
        transport.read_frame(read)
    assert_refusal(error.value, "truncated_body", {"declared_length": 4096, "received_bytes": 100})


# --------------------------------------------------------------------------------------------- #
# the bound, and refusing before allocating
# --------------------------------------------------------------------------------------------- #


def test_exactly_the_bound_is_accepted() -> None:
    document = document_of_payload_size(MAX)
    frame = transport.encode_frame(document)
    assert len(frame) == transport.FRAME_HEADER_BYTES + MAX
    payload = transport.read_frame(Recorder(frame))
    assert payload is not None and len(payload) == MAX
    assert transport.decode_frame(payload) == document


def test_one_byte_above_the_bound_is_refused() -> None:
    with pytest.raises(AirootError) as error:
        transport.encode_frame(document_of_payload_size(MAX + 1))
    assert_refusal(error.value, "too_large", {"declared_length": MAX + 1})

    read = Recorder(header(MAX + 1))
    with pytest.raises(AirootError) as error:
        transport.read_frame(read)
    assert_refusal(error.value, "too_large", {"declared_length": MAX + 1})
    assert read.requests == [transport.FRAME_HEADER_BYTES], "the body of an oversized frame is never asked for"


@pytest.mark.parametrize("length", [MAX + 1, MAX * 4, 0x7FFFFFFF, 0xFFFFFFFF])
def test_an_oversized_header_is_never_asked_for_the_body(length: int) -> None:
    """A bound enforced after the 4 GiB is in memory is not a bound. This is that property, measured."""

    read = Recorder(header(length))
    with pytest.raises(AirootError) as error:
        transport.read_frame(read)

    assert_refusal(error.value, "too_large", {"declared_length": length})
    assert read.requests == [transport.FRAME_HEADER_BYTES]
    assert max(read.requests) <= MAX, "the reader asked for more bytes than its own bound allows"


def test_a_zero_length_frame_is_refused_without_asking_for_a_body() -> None:
    read = Recorder(header(0))
    with pytest.raises(AirootError) as error:
        transport.read_frame(read)
    assert_refusal(error.value, "zero_length", {"declared_length": 0})
    assert read.requests == [transport.FRAME_HEADER_BYTES]


# --------------------------------------------------------------------------------------------- #
# every refusal: the rule, the code, and the numbers
# --------------------------------------------------------------------------------------------- #

#: One input per rule `read_frame` can refuse on, as data. The numbers are what the refusal must carry,
#: so a rule whose evidence stops saying *what* it saw reddens here rather than passing on wording.
READ_REFUSALS: dict[str, tuple[Callable[[], Recorder], dict[str, int]]] = {
    "short_header": (lambda: Recorder(b"\x01\x00"), {"received_bytes": 2}),
    "zero_length": (lambda: Recorder(header(0)), {"declared_length": 0}),
    "too_large": (lambda: Recorder(header(MAX + 1)), {"declared_length": MAX + 1}),
    "truncated_body": (
        lambda: Recorder(header(64) + b"x" * 10),
        {"declared_length": 64, "received_bytes": 10},
    ),
    # A `read` that hands back more than it was asked for has broken its own contract, and the bytes it
    # returned can no longer be framed. Refusing beats unpacking a five-byte header into a four-byte
    # format: that would surface as a `struct.error`, which is this build crashing on the caller's bug.
    "over_read": (
        lambda: Recorder(header(8) + b"x" * 8, over=1),
        {"declared_length": transport.FRAME_HEADER_BYTES, "received_bytes": transport.FRAME_HEADER_BYTES + 1},
    ),
}

#: One input per rule `decode_frame` can refuse on. `declared_length` is the payload it was handed, which
#: is the only number that layer can honestly report.
DECODE_REFUSALS: dict[str, tuple[bytes, dict[str, int]]] = {
    "not_utf8": (b'{"a":"\xff"}', {"declared_length": 9}),
    "not_json": (b'{"a":', {"declared_length": 5}),
    "not_object": (b"[1,2]", {"declared_length": 5}),
}


def test_every_frame_rule_is_reachable() -> None:
    """`FRAME_RULES` is the contract as data; this is what stops it becoming a list of good intentions."""

    reachable = set(READ_REFUSALS) | set(DECODE_REFUSALS)
    assert reachable == set(transport.FRAME_RULES), (
        "a rule with no input that reaches it, or an input for a rule that no longer exists: "
        f"{sorted(reachable ^ set(transport.FRAME_RULES))}"
    )


@pytest.mark.parametrize("rule", sorted(READ_REFUSALS))
def test_a_reader_refusal_names_its_rule(rule: str) -> None:
    make_read, numbers = READ_REFUSALS[rule]
    with pytest.raises(AirootError) as error:
        transport.read_frame(make_read())
    assert_refusal(error.value, rule, numbers)


@pytest.mark.parametrize("rule", sorted(DECODE_REFUSALS))
def test_a_decoder_refusal_names_its_rule(rule: str) -> None:
    payload, numbers = DECODE_REFUSALS[rule]
    with pytest.raises(AirootError) as error:
        transport.decode_frame(payload)
    assert_refusal(error.value, rule, numbers)


def test_a_short_header_says_how_short_it_was() -> None:
    for received in range(1, transport.FRAME_HEADER_BYTES):
        read = Recorder(b"\x01" * received)
        with pytest.raises(AirootError) as error:
            transport.read_frame(read)
        assert_refusal(error.value, "short_header", {"received_bytes": received})
        assert f"{received} of the {transport.FRAME_HEADER_BYTES} header bytes" in error.value.message


def test_no_refusal_is_anything_but_an_airoot_error() -> None:
    """`INVALID_INPUT` and nothing else: no `struct.error`, no `UnicodeDecodeError`, no `ValueError`."""

    inputs: list[Callable[[], object]] = [
        *(lambda make=make: transport.read_frame(make()) for make, _ in READ_REFUSALS.values()),
        *(lambda payload=payload: transport.decode_frame(payload) for payload, _ in DECODE_REFUSALS.values()),
        lambda: transport.decode_frame(b""),
        lambda: transport.read_frame(Recorder(b"\x00")),
    ]
    for call in inputs:
        with pytest.raises(AirootError) as error:
            call()
        assert error.value.exit_code == EXIT_INVALID_INPUT


# --------------------------------------------------------------------------------------------- #
# decoding: not UTF-8, not JSON, not an object
# --------------------------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", [b"[1,2]", b'"x"', b"null", b"1", b"true", b"[]", b'""'])
def test_a_json_payload_that_is_not_an_object_is_refused(payload: bytes) -> None:
    """All of these parse. None of them is a document, and letting one through pushes the type error
    into every field reader downstream — so it is refused here, as the caller's bad input."""

    with pytest.raises(AirootError) as error:
        transport.decode_frame(payload)
    assert_refusal(error.value, "not_object", {"declared_length": len(payload)})
    assert type(json.loads(payload.decode("utf-8"))).__name__ in error.value.message


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\xfe",
        b'{"a":"\xc3"}',  # a truncated two-byte sequence
        b'{"a":"\xed\xa0\x80"}',  # a surrogate half, which UTF-8 forbids
        b"\x80\x81",  # continuation bytes with no lead byte
        b"\xf5\x80\x80\x80",  # above U+10FFFF
    ],
)
def test_bytes_that_are_not_utf8_are_refused(payload: bytes) -> None:
    with pytest.raises(AirootError) as error:
        transport.decode_frame(payload)
    assert_refusal(error.value, "not_utf8", {"declared_length": len(payload)})


@pytest.mark.parametrize("payload", [b"", b"{", b"{}extra", b'{"a":1,}', b"nan"])
def test_bytes_that_are_not_json_are_refused(payload: bytes) -> None:
    with pytest.raises(AirootError) as error:
        transport.decode_frame(payload)
    assert_refusal(error.value, "not_json", {"declared_length": len(payload)})


# --------------------------------------------------------------------------------------------- #
# the boundary this layer does not cross
# --------------------------------------------------------------------------------------------- #


def test_a_read_that_itself_fails_is_not_re_coded_as_a_malformed_frame() -> None:
    """A broken pipe is the transport's failure, not the caller's document.

    The frozen reason table has no word for "the IPC stream failed", and `INVALID_INPUT` says the
    caller's input was wrong — which would blame the document for the pipe. So the exception travels,
    and this test is what keeps a later "wrap it in AirootError" from quietly mis-blaming a caller.
    """

    def read(n: int) -> bytes:
        raise OSError(109, "The pipe has been ended")

    with pytest.raises(OSError):
        transport.read_frame(read)


def test_the_layer_imports_nothing_that_can_open_a_pipe_or_elevate() -> None:
    """It is a byte contract, so the import list is the evidence — and exact, so a new one is a choice.

    Equality rather than a scan for suspicious names: `transport` must stay readable by a Rust port and
    usable by any caller that has bytes, and the way that stops being true is an import nobody noticed.
    """

    source = pathlib.Path(transport.__file__ or "").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported == {"__future__", "json", "struct", "collections.abc", "typing", "..exits"}
