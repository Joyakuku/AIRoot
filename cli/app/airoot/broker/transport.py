"""The broker's framing layer: one request is one frame — docs/broker §3, made a reader.

`protocol.py` is explicit that it is **not** the transport ("a request is a document, not a frame").
This module is the frame. docs/broker §3 states the rule it exists to keep:

    一次提交请求必须是一个完整 envelope，不能由客户端分段拼接安全字段

So a frame is exactly one `broker-request`, and :func:`read_frame` either hands back the whole payload
or refuses. There is no "read the safety fields from the next segment" path and no place to keep a
half-built document, which is the structural form of that rule rather than a check against breaking it.

The layout, as data, because the Rust service of ADR-0001 has to match it byte for byte::

    +--------------------------------+--------------------------------------+
    | 4 bytes: little-endian uint32  | `length` bytes: UTF-8 JSON, exactly  |
    | = `length` (FRAME_HEADER_BYTES)| one document                         |
    +--------------------------------+--------------------------------------+

Three things this module deliberately is **not**, each being the nearest misreading:

* **not authenticated, and not the trust boundary.** A frame that parses is a well-formed *claim*. The
  peer's token, the plan hash and whether the caller may ask at all belong to the service
  (`broker/policy.py`, docs/broker §4). What parsing here proves is that the bytes were a document.
* **not the schema check.** :func:`decode_frame` returns whatever JSON object the payload held; that it
  is a `broker-request` is `protocol.build_request` / `validate_self`'s question, not framing's. Keeping
  the two apart is what stops "the bytes parsed" from reading as "the request is valid".
* **not a session.** One call reads one frame. There is no buffer, no cursor and no segment state.

**Refuse before allocating** is what makes :data:`MAX_FRAME_BYTES` a bound instead of a post-mortem: a
header that declares more than the bound is refused *without asking `read` for the body at all*. A limit
enforced after the 4 GiB is in memory has already lost, and that pathology is what the Rust port would
inherit from a reader that "reads the length, reads the body, then checks the length".

Every refusal is the caller's input being malformed, so the code is `INVALID_INPUT` (exit 8) and the
evidence names the rule that failed; :data:`FRAME_RULES` is that rule set as data, so a test can prove
each one is reachable instead of trusting a list of assertions to be complete.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable
from typing import Any

from ..exits import AirootError

#: Bytes of length prefix before every payload: a little-endian uint32.
FRAME_HEADER_BYTES = 4

#: The header's layout as data. `struct` is the one place the width and byte order may be spelled, and
#: `struct.calcsize` is asserted against `FRAME_HEADER_BYTES` by the test suite so the two cannot drift.
LENGTH_FORMAT = "<I"

#: The largest single frame this layer will read or write, in bytes.
#:
#: The bound is derived from what a `broker-request` can actually carry, not picked for roundness
#: (docs/broker §3, `cli/schema/broker-request.schema.json`). The document is a protocol version, an
#: operation, a request id and an application id (both `common.$defs.id`: at most 128 characters), a
#: pid, an integrity level, a timestamp, and the two root-relative references the operation needs. Every
#: field is bounded by the schema **except** `plan_ref`/`approval_ref`/`transaction_id`, and the first
#: two are paths the broker must be able to open: `state/plans/…` under a root, which Windows caps at
#: 32 767 UTF-16 code units (the `\\?\` extended form `caps/search.py` already uses). A worst-case path
#: of astral-plane characters is 4 UTF-8 bytes per 2 code units, so each reference is at most 65 534
#: bytes and the pair at most ~131 KiB — 256 KiB is the smallest power of two that holds that, the JSON
#: overhead and any scalar field a later minor schema bump adds, and it is still small enough that
#: "refused before allocating" means memory a broker can hold. A frame above it could only be a document
#: naming a reference no Windows API could open, or a payload that is not a `broker-request` at all.
MAX_FRAME_BYTES = 262144

#: Every rule this layer can refuse on, as stable tokens (also carried in `details["rule"]`). The test
#: suite maps each token to the input that triggers it and asserts the map covers this tuple exactly,
#: so a rule added here without a test — or a refusal added as a bare `raise` with no token — is visible.
FRAME_RULES: tuple[str, ...] = (
    "zero_length",
    "too_large",
    "short_header",
    "truncated_body",
    "over_read",
    "not_utf8",
    "not_json",
    "not_object",
)

_HEADER = struct.Struct(LENGTH_FORMAT)


def encode_frame(document: dict) -> bytes:
    """Frame exactly one document: the length prefix, then its canonical UTF-8 JSON bytes.

    The byte form is deterministic, so a fixture of a frame is byte-stable: keys are sorted, separators
    are minimal and non-ASCII is kept as UTF-8 rather than escaped. **That canonical form is the
    sender's courtesy, not the contract** — it exists so two implementations comparing frames compare
    bytes, and :func:`decode_frame` must never depend on key order or on the spacing (JSON object order
    is not significant, and a peer written in any other language will not preserve ours).

    A document whose payload would exceed :data:`MAX_FRAME_BYTES` is refused rather than sent: the bound
    is a property of the format, and a sender that emits a frame this layer's own reader refuses has
    produced a stream that can only fail at the far end, where the reason is harder to see.
    """

    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise _refusal(
            "too_large",
            f"the document frames to {len(payload)} bytes, above the {MAX_FRAME_BYTES}-byte frame bound",
            declared_length=len(payload),
        )
    return _HEADER.pack(len(payload)) + payload


def read_frame(read: Callable[[int], bytes]) -> bytes | None:
    """Read one frame's payload from ``read``; ``None`` on a clean EOF before any header byte.

    ``read(n)`` is any callable that returns **at most** ``n`` bytes and ``b""`` at end of stream — a
    pipe or socket ``recv``, or a test's recorder. It is asked repeatedly, because "at most" is the
    whole contract: a header split across two reads and a body delivered in three are both normal, and
    a reader that assumes one call returns everything works on every fast local pipe and then truncates
    the first time a peer writes in pieces.

    A client that connected and said nothing returns ``None``: an empty stream is not a malformed frame,
    it is the ordinary shape of a peer that gave up, and turning it into an error would make a routine
    disconnect look like corruption. Every other outcome — a header that cannot be completed, a declared
    length of zero or above :data:`MAX_FRAME_BYTES`, a body that ends early, a ``read`` that returns more
    bytes than it was asked for — is an `AirootError` with `INVALID_INPUT` (exit 8) and the failing rule
    in its evidence.

    The bound is checked **before the body is requested**, so an oversized declaration costs no
    allocation and no read at all (see the module docstring). Nothing is returned until the whole
    payload is in hand, so no caller can observe a partially-built conversation.

    An exception raised by ``read`` itself (a broken pipe, a cancelled overlapped read) is *not* a
    malformed frame and is not re-coded here: the frozen reason table has no word for "the IPC stream
    failed", and `INVALID_INPUT` would blame the caller's document for the transport's failure. It
    propagates to whoever owns the stream.
    """

    header = _read_up_to(read, FRAME_HEADER_BYTES)
    if not header:
        return None
    if len(header) < FRAME_HEADER_BYTES:
        raise _refusal(
            "short_header",
            f"the stream ended after {len(header)} of the {FRAME_HEADER_BYTES} header bytes",
            declared_length=None,
            received=len(header),
        )

    (length,) = _HEADER.unpack(header)
    if length == 0:
        raise _refusal(
            "zero_length",
            "the header declares a zero-length payload, which is not a document",
            declared_length=0,
        )
    if length > MAX_FRAME_BYTES:
        raise _refusal(
            "too_large",
            f"the header declares {length} bytes, above the {MAX_FRAME_BYTES}-byte frame bound; "
            "the body was not read",
            declared_length=length,
        )

    payload = _read_up_to(read, length)
    if len(payload) != length:
        raise _refusal(
            "truncated_body",
            f"the header declares {length} bytes but the stream ended after {len(payload)}",
            declared_length=length,
            received=len(payload),
        )
    return payload


def decode_frame(payload: bytes) -> dict:
    """Decode one frame's payload into the JSON object it carries.

    Three refusals, all `INVALID_INPUT` (exit 8) with the rule in the evidence, because all three are
    the caller's bytes being wrong rather than this build being wrong:

    * the bytes are not UTF-8 (`not_utf8`);
    * they are not JSON (`not_json`);
    * they are JSON but not an **object** (`not_object`) — `[1,2]`, `"x"` and `null` all decode fine and
      none is a document, and accepting one would push a type error into every field reader downstream.

    What comes back is the decoded object and nothing more. Whether it is a `broker-request` is the
    protocol layer's question (see the module docstring), which is why no schema is named here.
    """

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _refusal(
            "not_utf8",
            "the payload is not valid UTF-8, so no document can be read from it",
            declared_length=len(payload),
        ) from exc

    try:
        document = json.loads(text)
    except ValueError as exc:
        raise _refusal(
            "not_json",
            f"the payload is not JSON: {exc}",
            declared_length=len(payload),
        ) from exc

    if not isinstance(document, dict):
        raise _refusal(
            "not_object",
            f"the payload is JSON {type(document).__name__}, not a JSON object",
            declared_length=len(payload),
        )
    return document


def _read_up_to(read: Callable[[int], bytes], count: int) -> bytes:
    """Ask ``read`` for at most ``count`` bytes until it delivers them all or reports end of stream.

    The loop is the point: ``read``'s contract is "at most ``n``", so a single call returning less than
    asked (a header split across two writes, a body that arrives in pieces) must continue rather than
    count as truncation. Fewer bytes than ``count`` come back only when the stream ended.

    More bytes than ``count`` is a `read` that has broken its own contract, and it is refused rather
    than accommodated: the bytes it returned can no longer be framed, and carrying on would either drop
    the excess (silently desynchronizing the stream) or unpack five bytes into a four-byte format and
    die of a `struct.error` — this build crashing on the caller's bug. A rule token of its own, so the
    refusal says which contract broke instead of looking like a malformed frame.
    """

    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = read(remaining)
        if not chunk:
            break
        if len(chunk) > remaining:
            raise _refusal(
                "over_read",
                f"read({remaining}) returned {len(chunk)} bytes, more than it was asked for",
                declared_length=remaining,
                received=len(chunk),
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _refusal(
    rule: str,
    message: str,
    *,
    declared_length: int | None,
    received: int | None = None,
) -> AirootError:
    """Build one framing refusal: `INVALID_INPUT`, the rule token, and what was actually seen.

    The rule is both a sentence and a stable token (`FRAME_RULES`), because a caller reading the error
    needs prose while a test — and the Rust port's own error type — needs something that does not move
    when the wording does. The numbers go in `details` as scalars, which is the shape
    `error-response.details` accepts.
    """

    evidence = [f"rule: {rule}", message]
    details: dict[str, Any] = {"rule": rule}
    if declared_length is not None:
        details["declared_length"] = declared_length
    if received is not None:
        details["received_bytes"] = received
    return AirootError("INVALID_INPUT", message, evidence=evidence, details=details)
