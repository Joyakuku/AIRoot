"""RFC 8032 Ed25519 — pure-Python key derivation, signing and verification.

This is the PureEdDSA instance RFC 8032 calls ``Ed25519``: :func:`generate_keypair`,
:func:`public_key_from_private`, :func:`sign` and :func:`verify`, with no third-party
dependency (``jsonschema`` stays the only one). It exists because the protected
approval boundary has to be able to *check* an ``ed25519`` approval token before any
real key can be protected, and because ``tx/approval.py`` currently has to refuse that
algorithm outright (ADR-0025 D1).

Conformance
-----------
The acceptance face is RFC 8032 §7.1's own test vectors, verbatim, in
``cli/tests/test_l1_ed25519.py``: TEST 1 (empty message), TEST 2, TEST 3 and TEST 1024
(a 1023-byte message). The arithmetic mirrors the reference algorithms the RFC cites —
point addition and doubling in extended twisted-Edwards coordinates for ``a = -1``
(``add-2008-hwcd`` / ``dbl-2008-hwcd``, RFC 8032 Appendix A) and SHA-512 for the two
hashes of §5.1.6/§5.1.7. Verification uses the group equation of §5.1.7
**without** the optional cofactor multiplication, i.e. the stricter of the two variants
the RFC allows; see :func:`verify` for what that buys and what it costs.

What this module is **not**
---------------------------
* **Not a protected key store.** A private key is 32 bytes the caller owns: this
  module neither reads, writes, protects nor ACLs one, and it has no opinion about
  where the key should live. The issuer's key is produced and guarded by the P2
  elevated broker — a boundary this module knows nothing about. It checks documents
  that boundary signs; it does not make that boundary trustworthy.
* **Not constant-time, and it does not claim to be.** Field arithmetic is Python
  big-integer arithmetic and every loop takes a branch per bit, so timings depend on
  the data. Two different consequences, stated separately because they are different
  risks:

  - Verification touches only *public* data — the signature, the public key and the
    message are all things an attacker already has. A timing signal there leaks
    nothing secret, which is why a pure-Python verifier is an acceptable place to
    stand.
  - Signing touches the *private* key, and its scalar multiplication's addition chain
    follows the bits of the (secret) scalar. Measured over Python's big integers the
    timing is also data-dependent in ways no branch-free rewriting of this code can
    fix. **A long-lived issuer key must not be signed with by this module**; that
    belongs to the protected issuer (Rust, per ADR-0001), where the implementer can
    use a hardened library. The signing path here exists so tests can produce a
    signature the verifier must accept, and for nothing else.
* **Not the whole trust decision.** :func:`verify` answers "did the holder of *this*
  key sign *this* message". It does not police which key you hand it: AIROOT's
  approval path must pin the issuer key from the protected boundary, never take one
  from the document it is checking. See :func:`verify` for the degenerate keys that
  makes a difference for.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = [
    "PRIVATE_KEY_SIZE",
    "PUBLIC_KEY_SIZE",
    "SEED_SIZE",
    "SIGNATURE_SIZE",
    "generate_keypair",
    "public_key_from_private",
    "sign",
    "verify",
]

#: Sizes in bytes. ``PRIVATE_KEY_SIZE`` and ``SEED_SIZE`` are the same number because
#: RFC 8032's Ed25519 secret key **is** the seed: the scalar is recomputed from
#: ``SHA-512(seed)`` on every use and never stored. A 64-byte "expanded" secret key is a
#: libsodium/ref10 convention, not an RFC 8032 one, and this API deliberately has no
#: such form — there is no way to hand :func:`sign` a pre-expanded scalar.
PRIVATE_KEY_SIZE = 32
PUBLIC_KEY_SIZE = 32
SEED_SIZE = 32
SIGNATURE_SIZE = 64

# Field and group constants (RFC 8032 §5.1, RFC 7748 §4.1).
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
#: 2^((p-1)/4), the square root of -1: the RFC's "x = 2^((p-1)/4) * x is a square root".
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z, T = XY/Z. Tuple, not a class:
# a point is passed around by value and never mutated, which keeps the arithmetic free of
# hidden state and the code readable next to the formulas it implements.
_Point = tuple


def _recover_x(y: int, sign: int) -> int | None:
    """The x-coordinate for ``y`` with the requested low bit, or ``None`` if there is none.

    RFC 8032 §5.1.3 steps 2-4. ``u/v`` is computed as ``u v^3 (u v^7)^((p-5)/8)``, which
    is the single-powering trick the RFC gives; ``v`` is never zero on this curve, so the
    inverse always exists and the only failures are "not a square" and "zero with a
    set sign bit".
    """

    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    v3 = v * v % _P * v % _P
    v7 = v3 * v3 % _P * v % _P
    x = u * v3 % _P * pow(u * v7 % _P, (_P - 5) // 8, _P) % _P
    vxx = v * x % _P * x % _P
    if vxx != u:
        if vxx != (_P - u) % _P:
            return None
        x = x * _SQRT_M1 % _P
        if v * x % _P * x % _P != u:
            return None
    if x == 0 and sign:
        # "If x = 0 and x_0 = 1, decoding fails": the point with y = +-1 has one x, and it
        # cannot have a sign bit (RFC 8032 §5.1.3 step 4).
        return None
    if x & 1 != sign:
        x = _P - x
    return x


def _decode_point(encoded: bytes) -> _Point | None:
    """Decode a 32-byte point encoding, or ``None`` — RFC 8032 §5.1.3, strictly.

    "Strictly" is three checks, all of them refusals rather than silent repairs:

    * the length must be exactly 32 bytes;
    * ``y`` must be **canonically encoded**, i.e. ``y < p`` — the RFC's "if the resulting
      value is >= p, decoding fails". Skipping this is how an implementation ends up
      accepting two different byte strings for one point;
    * the recovered ``x`` must exist, and a zero ``x`` must carry sign bit 0.
    """

    if len(encoded) != PUBLIC_KEY_SIZE:
        return None
    value = int.from_bytes(encoded, "little")
    sign = (value >> 255) & 1
    y = value & ((1 << 255) - 1)
    if y >= _P:
        return None
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _encode_point(point: _Point) -> bytes:
    """The 32-byte encoding of a point: little-endian ``y`` with ``x``'s low bit on top.

    ``Z`` is never zero here: every point entering the arithmetic comes either from
    :func:`_decode_point` (which returns ``Z = 1``) or from the neutral element, and the
    addition/doubling formulas are complete, so no step can produce ``Z = 0`` for a valid
    point. ``pow(0, p - 2, p)`` would silently answer 0 rather than fail, which is why the
    invariant is worth stating rather than testing for.
    """

    x, y, z, _t = point
    z_inv = pow(z, _P - 2, _P)
    x = x * z_inv % _P
    y = y * z_inv % _P
    return (y | ((x & 1) << 255)).to_bytes(PUBLIC_KEY_SIZE, "little")


def _add(p: _Point, q: _Point) -> _Point:
    """Extended-coordinate addition, complete for every input (``add-2008-hwcd``, a = -1).

    The factors are ``(Y1 - X1)(Y2 - X2)`` and ``(Y1 + X1)(Y2 + X2)`` with the ``2d`` and
    ``2Z1Z2`` constants of the RFC 8032 Appendix A spelling; they compute four times the
    coordinates of the EFD formula, which is the same point (``X : Y : Z : T`` is
    homogeneous). No special case is needed for ``p == q`` or for the neutral element,
    which is why there is no branch here to get wrong.
    """

    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * _D * t1 * t2 % _P
    d = 2 * z1 * z2 % _P
    e = b - a
    h = b + a
    f = d - c
    g = d + c
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _double(p: _Point) -> _Point:
    """Extended-coordinate doubling (``dbl-2008-hwcd`` with a = -1)."""

    x1, y1, z1, _t1 = p
    a = x1 * x1 % _P
    b = y1 * y1 % _P
    c = 2 * z1 * z1 % _P
    h = a + b
    e = h - (x1 + y1) ** 2
    g = a - b
    f = c + g
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _negate(p: _Point) -> _Point:
    """The inverse of a point: ``(x, y) -> (-x, y)``."""

    x, y, z, t = p
    return ((-x) % _P, y, z, (-t) % _P)


_IDENTITY: _Point = (0, 1, 1, 0)


def _scalar_mult(point: _Point, scalar: int) -> _Point:
    """``[scalar]point`` by double-and-add, least significant bit first."""

    result = _IDENTITY
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _double(addend)
        scalar >>= 1
    return result


def _double_scalar_mult(s1: int, p1: _Point, s2: int, p2: _Point) -> _Point:
    """``[s1]p1 + [s2]p2`` over one shared doubling chain.

    Verification needs exactly this shape, and sharing the chain makes it about a third
    cheaper than two independent scalar multiplications: 255 doublings instead of 510,
    with the same number of additions. The scalars are secrets only in :func:`sign`,
    which does not use this helper.
    """

    result = _IDENTITY
    for bit in range(max(s1.bit_length(), s2.bit_length()) - 1, -1, -1):
        result = _double(result)
        if (s1 >> bit) & 1:
            result = _add(result, p1)
        if (s2 >> bit) & 1:
            result = _add(result, p2)
    return result


_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = _recover_x(_BY, 0)
if _BX is None:  # pragma: no cover - unreachable: the base point is on the curve
    raise RuntimeError("ed25519 base point does not decode")
# RFC 8032 §5.1 Table 1 gives B as the RFC 7748 base point: y = 4/5, x the even root.
_BASE: _Point = (_BX, _BY, 1, _BX * _BY % _P)


def _clamped_scalar(private_key: bytes) -> tuple[int, bytes]:
    """``SHA-512(seed)`` split into the pruned scalar and the nonce prefix (§5.1.5).

    Pruning is "clear the three low bits, clear bit 255, set bit 254": the low bits make
    the scalar a multiple of the cofactor, the top two bits fix its length so that the
    multiplication cannot be shortened.
    """

    digest = hashlib.sha512(private_key).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def _require_size(kind: str, value: object, size: int, expected: str) -> bytes:
    """Reject a wrongly sized key instead of padding or truncating it."""

    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"{kind} must be bytes, not {type(value).__name__}")
    if len(value) != size:
        raise ValueError(f"{kind} must be exactly {size} bytes ({expected}); got {len(value)}")
    return bytes(value)


def _require_message(message: object) -> bytes:
    if not isinstance(message, (bytes, bytearray, memoryview)):
        raise ValueError(f"message must be bytes, not {type(message).__name__}")
    return bytes(message)


def generate_keypair(seed: bytes) -> tuple[bytes, bytes]:
    """``(private_key, public_key)`` derived deterministically from a 32-byte seed.

    The private key returned is the seed itself (see :data:`PRIVATE_KEY_SIZE`), so this
    function is a derivation of the public half, not a key expansion: calling it twice
    with one seed returns equal pairs. A ``seed`` whose length is not 32 bytes is a
    ``ValueError``; RFC 8032 defines no other seed size, and silently hashing a short
    seed would invent one.
    """

    seed_bytes = _require_size("seed", seed, SEED_SIZE, "RFC 8032 Ed25519 secret key")
    return seed_bytes, public_key_from_private(seed_bytes)


def public_key_from_private(private_key: bytes) -> bytes:
    """``ENC([s]B)`` for the scalar ``s`` that ``SHA-512(private_key)`` determines.

    A ``private_key`` that is not exactly 32 bytes is a ``ValueError``.
    """

    key = _require_size("private_key", private_key, PRIVATE_KEY_SIZE, "RFC 8032 Ed25519 secret key")
    scalar, _prefix = _clamped_scalar(key)
    return _encode_point(_scalar_mult(_BASE, scalar))


def sign(private_key: bytes, message: bytes) -> bytes:
    """A 64-byte RFC 8032 signature over ``message``.

    The signature is ``ENC(R) || ENC(S)`` with the deterministic nonce of §5.1.6, so the
    same key and message always produce the same 64 bytes.

    Raises ``ValueError`` when ``private_key`` is not exactly 32 bytes or ``message`` is
    not bytes. It never pads, truncates or reinterprets either one.

    Timing: this function handles the private key, and it is **not** constant-time. The
    scalar multiplication's addition chain follows the bits of the secret scalar and
    Python's big-integer arithmetic does not have data-independent timing either. Do not
    use this path for a long-lived issuer key; the protected issuer (P2, Rust) is where
    that key is used. See the module docstring.
    """

    key = _require_size("private_key", private_key, PRIVATE_KEY_SIZE, "RFC 8032 Ed25519 secret key")
    body = _require_message(message)
    scalar, prefix = _clamped_scalar(key)
    public_key = _encode_point(_scalar_mult(_BASE, scalar))
    # r = SHA-512(prefix || M) mod L; R = [r]B; S = (r + SHA-512(R || A || M) * s) mod L.
    r = int.from_bytes(hashlib.sha512(prefix + body).digest(), "little") % _L
    encoded_r = _encode_point(_scalar_mult(_BASE, r))
    k = int.from_bytes(hashlib.sha512(encoded_r + public_key + body).digest(), "little") % _L
    s = (r + k * scalar) % _L
    return encoded_r + s.to_bytes(32, "little")


def verify(public_key: bytes, signature: bytes, message: bytes) -> bool:
    """True only for a signature this key made over this message. Never raises.

    Every refusal is a ``False``, including the malformed ones a caller is most likely to
    hand over by accident — a ``public_key`` that is not 32 bytes, a ``signature`` that is
    not 64 bytes, a message that is not bytes, an encoding that is not a curve point, a
    ``y`` that is not canonically encoded, and an ``S`` outside ``[0, L)``. The docstrings
    of :func:`sign` and :func:`generate_keypair` say ``ValueError`` where a bad *key* is
    the caller's mistake; here a bad value is the answer "no", because this runs on
    untrusted input and a crash in a security path is worse than a refusal. The whole body
    is guarded, so the ``except`` is the last line of defence rather than the validation —
    and it fails closed, which means a defect in this file shows up as "everything is
    refused" in the test vectors, never as "everything is accepted".

    The equation checked is ``[S]B = R + [k]A`` (§5.1.7), **without** the cofactor
    multiplication the RFC permits as an alternative ("it is sufficient, but not required,
    to instead check ..."). The two are not equivalent: multiplying both sides by the
    cofactor accepts any pair whose sides differ by a point of order dividing 8, and with a
    small-order public key it accepts a signature nobody ever produced. This module is on
    an approval path, so it takes the stricter reading, and
    ``test_a_signature_that_only_satisfies_the_cofactored_equation_is_refused`` builds that
    witness so the choice is measured rather than asserted.

    Consequences a caller should know rather than discover:

    * ``verify`` does not reject small-order *public keys*, so it is not the thing that
      decides which key is the issuer's. Under a degenerate key the equation is trivially
      satisfiable: with the neutral element as ``A``, ``[k]A`` is the identity and any
      ``(R = [S]B, S)`` verifies for **any** message, without a private key. Pin the
      issuer key (from the protected boundary, never from the document being checked) and
      this module only ever has to be right about a key that really is somebody's.
    * Messages are not length-limited or hashed twice: this is PureEdDSA, so there is no
      prehash and no ``dom2`` context. Ed25519ctx and Ed25519ph are *different*
      algorithms, and their RFC 8032 §7.2/§7.3 vectors do not apply here.
    """

    try:
        if not isinstance(public_key, (bytes, bytearray, memoryview)):
            return False
        if not isinstance(signature, (bytes, bytearray, memoryview)):
            return False
        if not isinstance(message, (bytes, bytearray, memoryview)):
            return False
        if len(public_key) != PUBLIC_KEY_SIZE or len(signature) != SIGNATURE_SIZE:
            return False
        public_key = bytes(public_key)
        signature = bytes(signature)
        message = bytes(message)

        point_a = _decode_point(public_key)
        if point_a is None:
            return False
        encoded_r = signature[:PUBLIC_KEY_SIZE]
        point_r = _decode_point(encoded_r)
        if point_r is None:
            return False
        s = int.from_bytes(signature[PUBLIC_KEY_SIZE:], "little")
        if s >= _L:
            return False

        k = int.from_bytes(hashlib.sha512(encoded_r + public_key + message).digest(), "little") % _L
        expected = _encode_point(_double_scalar_mult(s, _BASE, k, _negate(point_a)))
        # compare_digest, not ==: the values are public, so this is hygiene rather than a
        # defence, but an approval path should not be the place where a habit is broken.
        return hmac.compare_digest(expected, encoded_r)
    except Exception:  # noqa: BLE001 - fail closed on anything unforeseen (see docstring)
        return False
