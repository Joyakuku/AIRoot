"""L1: our Ed25519 against RFC 8032 — ``cli/app/airoot/crypto/ed25519.py``.

Why this file is shaped the way it is
-------------------------------------
"AIROOT has Ed25519" is a claim about a standard, and the only thing that makes it a
claim about the standard rather than about this implementation is RFC 8032 §7.1's test
vectors, verbatim. They are the first group below and they are the acceptance face:

* **TEST 1** — empty message;
* **TEST 2** — one byte;
* **TEST 3** — two bytes;
* **TEST 1024** — a 1023-byte message (the RFC's name is the buffer size, not the
  message length);
* **TEST SHA(abc)** — a 64-byte message, which the RFC states as SHA-512("abc").

Those five are **all** of §7.1. §7.2 (Ed25519ctx) and §7.3 (Ed25519ph) are *different
algorithms* — both prepend the ``dom2`` context RFC 8032 §2 defines so that they are not
Ed25519 — so they are not in the vector table; two tests below present those vectors and
require them to be **refused**, which is the honest way to record the difference.

Everything after the vectors is either a negative direction (a mutation that must be
refused, one small test per property so a failure names it) or an input the vectors
cannot see (wrong lengths, non-canonical encodings, types that are not bytes at all).
The suite is pure computation over constants in this file: no root, no registry, no
host state, no network. It runs in about a second, and the slowest test is deliberately
the one that flips every one of the signature's 512 bits.
"""

from __future__ import annotations

import ast
import hashlib
import random
from pathlib import Path

import pytest

from airoot.crypto import ed25519

#: A fixed seed for every test that needs "some key": reproducible, and unrelated to any
#: real one. The RFC seeds are used where the RFC is the authority.
_TEST_SEED = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")

#: RFC 8032 §5.1 Table 1, restated here **independently** so that the malleability test
#: compares the module against the RFC rather than against the module's own constant.
RFC_GROUP_ORDER_L = 2**252 + 27742317777372353535851937790883648493

#: The neutral element's encoding: y = 1, sign bit 0. A *valid* point encoding (it is the
#: group identity), which is exactly why it matters that it decodes.
IDENTITY_POINT = (1).to_bytes(32, "little")

#: y = 2: (y^2 - 1) / (d y^2 + 1) is a non-square modulo p, so no x exists and the
#: encoding is not a point. (y = 7, 8, 11, 12, ... are the same; 2 is the smallest.)
NOT_A_POINT = (2).to_bytes(32, "little")

#: The smallest **non-canonical** encoding: y = p, which is >= p and must be refused
#: rather than reduced (RFC 8032 §5.1.3 step 1).
NON_CANONICAL_Y = (2**255 - 19).to_bytes(32, "little")

#: A second non-canonical encoding, all bits set: sign bit 1 with y = 2^255 - 1 >= p.
NON_CANONICAL_ALL_ONES = b"\xff" * 32


# ---------------------------------------------------------------------------------------
# RFC 8032 §7.1, verbatim. This table was extracted from the RFC text rather than retyped,
# and each test below re-derives one column from the others, so a wrong digit cannot
# survive: the derivation, the signature and the verification would disagree.
# ---------------------------------------------------------------------------------------
RFC_8032_SECTION_7_1: list[tuple[str, bytes, bytes, bytes, bytes]] = [
    (
        "TEST 1",
        # secret key
        bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"),
        # public key
        bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"),
        # message (length 0)
        bytes.fromhex(""),
        # signature
        bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    ),
    (
        "TEST 2",
        bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb"),
        bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"),
        bytes.fromhex("72"),
        bytes.fromhex(
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    ),
    (
        "TEST 3",
        bytes.fromhex("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7"),
        bytes.fromhex("fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025"),
        bytes.fromhex("af82"),
        bytes.fromhex(
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    ),
    (
        "TEST 1024",
        bytes.fromhex("f5e5767cf153319517630f226876b86c8160cc583bc013744c6bf255f5cc0ee5"),
        bytes.fromhex("278117fc144c72340f67d0f2316e8386ceffbf2b2428c9c51fef7c597f1d426e"),
        bytes.fromhex(
            "08b8b2b733424243760fe426a4b54908632110a66c2f6591eabd3345e3e4eb98"
            "fa6e264bf09efe12ee50f8f54e9f77b1e355f6c50544e23fb1433ddf73be84d8"
            "79de7c0046dc4996d9e773f4bc9efe5738829adb26c81b37c93a1b270b20329d"
            "658675fc6ea534e0810a4432826bf58c941efb65d57a338bbd2e26640f89ffbc"
            "1a858efcb8550ee3a5e1998bd177e93a7363c344fe6b199ee5d02e82d522c4fe"
            "ba15452f80288a821a579116ec6dad2b3b310da903401aa62100ab5d1a36553e"
            "06203b33890cc9b832f79ef80560ccb9a39ce767967ed628c6ad573cb116dbef"
            "efd75499da96bd68a8a97b928a8bbc103b6621fcde2beca1231d206be6cd9ec7"
            "aff6f6c94fcd7204ed3455c68c83f4a41da4af2b74ef5c53f1d8ac70bdcb7ed1"
            "85ce81bd84359d44254d95629e9855a94a7c1958d1f8ada5d0532ed8a5aa3fb2"
            "d17ba70eb6248e594e1a2297acbbb39d502f1a8c6eb6f1ce22b3de1a1f40cc24"
            "554119a831a9aad6079cad88425de6bde1a9187ebb6092cf67bf2b13fd65f270"
            "88d78b7e883c8759d2c4f5c65adb7553878ad575f9fad878e80a0c9ba63bcbcc"
            "2732e69485bbc9c90bfbd62481d9089beccf80cfe2df16a2cf65bd92dd597b07"
            "07e0917af48bbb75fed413d238f5555a7a569d80c3414a8d0859dc65a46128ba"
            "b27af87a71314f318c782b23ebfe808b82b0ce26401d2e22f04d83d1255dc51a"
            "ddd3b75a2b1ae0784504df543af8969be3ea7082ff7fc9888c144da2af58429e"
            "c96031dbcad3dad9af0dcbaaaf268cb8fcffead94f3c7ca495e056a9b47acdb7"
            "51fb73e666c6c655ade8297297d07ad1ba5e43f1bca32301651339e22904cc8c"
            "42f58c30c04aafdb038dda0847dd988dcda6f3bfd15c4b4c4525004aa06eeff8"
            "ca61783aacec57fb3d1f92b0fe2fd1a85f6724517b65e614ad6808d6f6ee34df"
            "f7310fdc82aebfd904b01e1dc54b2927094b2db68d6f903b68401adebf5a7e08"
            "d78ff4ef5d63653a65040cf9bfd4aca7984a74d37145986780fc0b16ac451649"
            "de6188a7dbdf191f64b5fc5e2ab47b57f7f7276cd419c17a3ca8e1b939ae49e4"
            "88acba6b965610b5480109c8b17b80e1b7b750dfc7598d5d5011fd2dcc5600a3"
            "2ef5b52a1ecc820e308aa342721aac0943bf6686b64b2579376504ccc493d97e"
            "6aed3fb0f9cd71a43dd497f01f17c0e2cb3797aa2a2f256656168e6c496afc5f"
            "b93246f6b1116398a346f1a641f3b041e989f7914f90cc2c7fff357876e506b5"
            "0d334ba77c225bc307ba537152f3f1610e4eafe595f6d9d90d11faa933a15ef1"
            "369546868a7f3a45a96768d40fd9d03412c091c6315cf4fde7cb68606937380d"
            "b2eaaa707b4c4185c32eddcdd306705e4dc1ffc872eeee475a64dfac86aba41c"
            "0618983f8741c5ef68d3a101e8a3b8cac60c905c15fc910840b94c00a0b9d0"
        ),
        bytes.fromhex(
            "0aab4c900501b3e24d7cdf4663326a3a87df5e4843b2cbdb67cbf6e460fec350"
            "aa5371b1508f9f4528ecea23c436d94b5e8fcd4f681e30a6ac00a9704a188a03"
        ),
    ),
    (
        "TEST SHA(abc)",
        bytes.fromhex("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42"),
        bytes.fromhex("ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf"),
        bytes.fromhex(
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
            "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f"
        ),
        bytes.fromhex(
            "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
            "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"
        ),
    ),
]

RFC_VECTOR_IDS = [vector[0] for vector in RFC_8032_SECTION_7_1]


def _flip_bit(data: bytes, index: int) -> bytes:
    """``data`` with bit ``index`` flipped (bits counted little-endian inside each byte)."""

    mutated = bytearray(data)
    mutated[index // 8] ^= 1 << (index % 8)
    return bytes(mutated)


def _message(length: int) -> bytes:
    """A deterministic message of ``length`` bytes; distinct for every length."""

    return bytes((index * 31 + length) % 256 for index in range(length))


def _valid_signature() -> tuple[bytes, bytes, bytes, bytes]:
    """(private key, public key, message, signature) that verifies — the mutations mutate it."""

    private_key, public_key = ed25519.generate_keypair(_TEST_SEED)
    message = b"approval-token: plan/2f1c9a"
    return private_key, public_key, message, ed25519.sign(private_key, message)


# ---------------------------------------------------------------------------------------
# The vector table itself
# ---------------------------------------------------------------------------------------


def test_the_table_holds_exactly_the_five_vectors_of_section_7_1() -> None:
    """A truncated paste is a silent loss of acceptance evidence, so the data is checked too."""

    assert RFC_VECTOR_IDS == ["TEST 1", "TEST 2", "TEST 3", "TEST 1024", "TEST SHA(abc)"]
    assert [(len(key), len(pk), len(msg), len(sig)) for _, key, pk, msg, sig in RFC_8032_SECTION_7_1] == [
        (32, 32, 0, 64),
        (32, 32, 1, 64),
        (32, 32, 2, 64),
        (32, 32, 1023, 64),
        (32, 32, 64, 64),
    ]
    # "TEST SHA(abc)" states its message as SHA-512("abc"); recomputing it proves the column
    # is the RFC's prehash and not a transcription of some other 64 bytes.
    assert RFC_8032_SECTION_7_1[4][3] == hashlib.sha512(b"abc").digest()


@pytest.mark.parametrize("vector", RFC_8032_SECTION_7_1, ids=RFC_VECTOR_IDS)
def test_section_7_1_public_keys_derive_from_their_secret_keys(
    vector: tuple[str, bytes, bytes, bytes, bytes],
) -> None:
    """RFC 8032 §5.1.5: A = ENC([s]B) for the pruned scalar s of SHA-512(secret)."""

    _name, secret_key, public_key, _message_, _signature = vector
    assert ed25519.public_key_from_private(secret_key) == public_key


@pytest.mark.parametrize("vector", RFC_8032_SECTION_7_1, ids=RFC_VECTOR_IDS)
def test_section_7_1_signatures_are_reproduced_byte_for_byte(
    vector: tuple[str, bytes, bytes, bytes, bytes],
) -> None:
    """RFC 8032 §5.1.6. Ed25519 is deterministic, so equality — not "verifies" — is the test."""

    _name, secret_key, _public_key, message, signature = vector
    assert ed25519.sign(secret_key, message) == signature


@pytest.mark.parametrize("vector", RFC_8032_SECTION_7_1, ids=RFC_VECTOR_IDS)
def test_section_7_1_signatures_verify(
    vector: tuple[str, bytes, bytes, bytes, bytes],
) -> None:
    _name, _secret_key, public_key, message, signature = vector
    assert ed25519.verify(public_key, signature, message) is True


@pytest.mark.parametrize("vector", RFC_8032_SECTION_7_1, ids=RFC_VECTOR_IDS)
def test_generate_keypair_is_the_rfc_keypair_for_the_rfc_seed(
    vector: tuple[str, bytes, bytes, bytes, bytes],
) -> None:
    """The derivation is proven by the RFC's keypair, not by round-tripping our own output.

    The private key returned is the seed itself (RFC 8032's secret key *is* the seed), and
    the public half must be the one the RFC publishes — which the round trip could never
    show, since a wrong derivation would happily round-trip with itself.
    """

    _name, secret_key, public_key, _message_, _signature = vector
    assert ed25519.generate_keypair(secret_key) == (secret_key, public_key)


def test_the_frozen_sizes_are_the_rfc_ones() -> None:
    """Another workstream wires this in against these names; a silent change here is a break."""

    assert ed25519.PRIVATE_KEY_SIZE == 32
    assert ed25519.PUBLIC_KEY_SIZE == 32
    assert ed25519.SEED_SIZE == 32
    assert ed25519.SIGNATURE_SIZE == 64
    # The private key is the seed: there is no separate 64-byte expanded secret key here.
    assert ed25519.PRIVATE_KEY_SIZE == ed25519.SEED_SIZE


def test_the_module_imports_nothing_outside_the_standard_library() -> None:
    """The whole point of a pure-Python curve: ``jsonschema`` stays the only dependency."""

    source = Path(ed25519.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "hashlib", "hmac"}, f"unexpected imports: {sorted(imported)}"


# ---------------------------------------------------------------------------------------
# The round trip, including the lengths the vectors do not cover
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("length", [0, 1, 32, 64, 127, 128, 129, 1023, 1024])
def test_sign_and_verify_round_trip_for_several_message_lengths(length: int) -> None:
    """Empty, one byte, and both sides of the 128-byte SHA-512 block the message is folded into."""

    private_key, public_key = ed25519.generate_keypair(_TEST_SEED)
    message = _message(length)
    signature = ed25519.sign(private_key, message)
    assert len(signature) == ed25519.SIGNATURE_SIZE
    assert ed25519.verify(public_key, signature, message) is True


def test_signing_the_same_message_twice_gives_the_same_signature() -> None:
    """Ed25519's nonce is derived from the key and the message, so there is no randomness to test."""

    private_key, _public_key, message, signature = _valid_signature()
    assert ed25519.sign(private_key, message) == signature


def test_verify_accepts_bytes_like_input() -> None:
    """``bytearray`` and ``memoryview`` are what a file reader hands over; they are the same bytes."""

    _private_key, public_key, message, signature = _valid_signature()
    assert ed25519.verify(bytearray(public_key), bytearray(signature), bytearray(message)) is True
    assert ed25519.verify(memoryview(public_key), memoryview(signature), memoryview(message)) is True


# ---------------------------------------------------------------------------------------
# Red directions: one small test per property, so a failure names what broke
# ---------------------------------------------------------------------------------------


def test_every_signature_bit_flip_is_refused() -> None:
    """All 512 bits of the signature, one at a time. The slowest test in the file, on purpose."""

    _private_key, public_key, message, signature = _valid_signature()
    assert ed25519.verify(public_key, signature, message) is True
    accepted = [
        index
        for index in range(8 * ed25519.SIGNATURE_SIZE)
        if ed25519.verify(public_key, _flip_bit(signature, index), message)
    ]
    assert accepted == [], f"bit positions that still verified: {accepted}"


def test_every_message_bit_flip_is_refused() -> None:
    """Five bytes of message, all 40 bits: the signature is bound to the message, not to a nonce."""

    private_key, public_key = ed25519.generate_keypair(_TEST_SEED)
    message = b"five!"
    signature = ed25519.sign(private_key, message)
    assert ed25519.verify(public_key, signature, message) is True
    accepted = [
        index
        for index in range(8 * len(message))
        if ed25519.verify(public_key, signature, _flip_bit(message, index))
    ]
    assert accepted == [], f"bit positions that still verified: {accepted}"


def test_a_signature_is_refused_by_a_different_public_key() -> None:
    """Both directions: a valid signature is not valid under someone else's key."""

    first_private, first_public = ed25519.generate_keypair(_TEST_SEED)
    second_private, second_public = ed25519.generate_keypair(bytes(32))
    message = b"bound to one key"
    first_signature = ed25519.sign(first_private, message)
    second_signature = ed25519.sign(second_private, message)
    assert first_public != second_public
    assert ed25519.verify(first_public, second_signature, message) is False
    assert ed25519.verify(second_public, first_signature, message) is False


@pytest.mark.parametrize("size", [0, 32, 63, 65, 128])
def test_a_truncated_or_extended_signature_is_refused(size: int) -> None:
    _private_key, public_key, message, signature = _valid_signature()
    assert size != ed25519.SIGNATURE_SIZE  # the case is "not 64", and 64 is the only accepted length
    candidate = (signature + b"\x00" * 64)[:size]
    assert ed25519.verify(public_key, candidate, message) is False


@pytest.mark.parametrize("size", [0, 31, 33, 64])
def test_a_public_key_of_the_wrong_size_is_refused(size: int) -> None:
    _private_key, public_key, message, signature = _valid_signature()
    candidate = (public_key + bytes(64))[:size]
    assert ed25519.verify(candidate, signature, message) is False


def test_a_public_key_that_is_not_a_curve_point_is_refused() -> None:
    """``y = 2`` has no x: the RFC's decoding must fail rather than pick a candidate.

    ``verify`` has more than one reason to say no to this input, so the rule itself is pinned
    separately in ``test_decoding_refuses_bad_point_encodings_at_the_layer_that_owns_the_rule``.
    """

    _private_key, _public_key, message, signature = _valid_signature()
    assert ed25519.verify(NOT_A_POINT, signature, message) is False


def test_a_public_key_that_is_not_canonically_encoded_is_refused() -> None:
    """y = p and y = 2^255 - 1 are both >= p; reducing them would accept two spellings of one point.

    Kept next to the decoding test for the same reason as the test above: the *refusal* is
    what a caller observes, and the canonicality rule is what makes it a refusal at the byte
    level rather than a wrong point being verified against.
    """

    _private_key, _public_key, message, signature = _valid_signature()
    assert ed25519.verify(NON_CANONICAL_Y, signature, message) is False
    assert ed25519.verify(NON_CANONICAL_ALL_ONES, signature, message) is False


def test_a_signature_whose_r_is_not_a_point_is_refused() -> None:
    """The first half of a signature is a point encoding, not an opaque 32 bytes.

    The decoding rule behind this is pinned in
    ``test_decoding_refuses_bad_point_encodings_at_the_layer_that_owns_the_rule``.
    """

    _private_key, public_key, message, signature = _valid_signature()
    assert ed25519.verify(public_key, NOT_A_POINT + signature[32:], message) is False


def test_a_signature_whose_r_is_not_canonically_encoded_is_refused() -> None:
    _private_key, public_key, message, signature = _valid_signature()
    assert ed25519.verify(public_key, NON_CANONICAL_Y + signature[32:], message) is False
    assert ed25519.verify(public_key, NON_CANONICAL_ALL_ONES + signature[32:], message) is False


def test_a_signature_whose_s_is_out_of_range_is_refused() -> None:
    """``S + L`` is the same scalar modulo L.

    An implementation that reduces S before comparing would accept this, and would then
    have handed every caller a second valid signature for one message — signature
    malleability, which RFC 8032 §8.4 exists to rule out. The byte string stays 32 bytes,
    so only the range check can catch it.
    """

    _private_key, public_key, message, signature = _valid_signature()
    s = int.from_bytes(signature[32:], "little")
    assert 0 <= s < RFC_GROUP_ORDER_L
    malleated = signature[:32] + (s + RFC_GROUP_ORDER_L).to_bytes(32, "little")
    assert len(malleated) == ed25519.SIGNATURE_SIZE
    assert ed25519.verify(public_key, malleated, message) is False


def test_a_signature_whose_s_is_the_largest_32_byte_value_is_refused() -> None:
    _private_key, public_key, message, signature = _valid_signature()
    assert ed25519.verify(public_key, signature[:32] + b"\xff" * 32, message) is False


def test_decoding_refuses_bad_point_encodings_at_the_layer_that_owns_the_rule() -> None:
    """A white-box test, and the reason for it is a measurement rather than a preference.

    ``verify`` refuses a non-canonical or non-point encoding for **several** reasons at once:
    the hash input contains the raw public-key bytes (so a re-spelled key changes ``k``), and
    the recomputed ``R`` is compared to the received ``R`` as bytes (so a re-spelled ``R``
    differs even when the point is the same). Deleting the ``y >= p`` refusal from
    ``_decode_point`` therefore leaves every ``verify``-level test in this file green — a test
    that cannot go red is not evidence. The rule is pinned here, where decoding is the only
    thing under test; the callers' behaviour is pinned by the tests around it.
    """

    # Positive controls first: the assertions below must not be able to pass by decoding
    # nothing at all. A real public key and the neutral element both decode.
    assert ed25519._decode_point(RFC_8032_SECTION_7_1[0][2]) is not None
    assert ed25519._decode_point(IDENTITY_POINT) is not None
    # Not 32 bytes: a 33-byte string would otherwise lose its top byte to the sign-bit mask.
    assert ed25519._decode_point(RFC_8032_SECTION_7_1[0][2] + b"\x00") is None
    assert ed25519._decode_point(RFC_8032_SECTION_7_1[0][2][:-1]) is None
    assert ed25519._decode_point(b"") is None
    # Not a point: (y^2 - 1) / (d y^2 + 1) is a non-square, so there is no x to recover.
    assert ed25519._decode_point(NOT_A_POINT) is None
    # Not canonical: y = p is zero modulo p, which *would* decode if it were reduced, and
    # y = 2^255 - 1 is eighteen mod p. Both must fail on the range rule alone.
    assert ed25519._decode_point(NON_CANONICAL_Y) is None
    assert ed25519._decode_point(NON_CANONICAL_ALL_ONES) is None


@pytest.mark.parametrize(
    "public_key, signature, message",
    [
        pytest.param(None, b"\x00" * 64, b"", id="public-key-is-none"),
        pytest.param("", b"\x00" * 64, b"", id="public-key-is-str"),
        pytest.param(1234, b"\x00" * 64, b"", id="public-key-is-int"),
        pytest.param(b"\x01" * 32, None, b"", id="signature-is-none"),
        pytest.param(b"\x01" * 32, "x" * 64, b"", id="signature-is-str"),
        pytest.param(b"\x01" * 32, [0] * 64, b"", id="signature-is-list"),
        pytest.param(b"\x01" * 32, b"\x00" * 64, None, id="message-is-none"),
        pytest.param(b"\x01" * 32, b"\x00" * 64, "text", id="message-is-str"),
        pytest.param(b"\x01" * 32, b"\x00" * 64, 42, id="message-is-int"),
        pytest.param(b"", b"", b"", id="everything-empty"),
    ],
)
def test_verify_returns_false_instead_of_raising_for_unusable_input(
    public_key: object, signature: object, message: object
) -> None:
    """A crash is not an answer. ``verify`` is on a security path: it fails closed."""

    assert ed25519.verify(public_key, signature, message) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# Key handling: bad *keys* are the caller's mistake and say so
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0, 16, 31, 33, 64])
def test_sign_rejects_a_private_key_of_the_wrong_size(size: int) -> None:
    with pytest.raises(ValueError):
        ed25519.sign(b"\x00" * size, b"message")


def test_sign_does_not_pad_a_short_private_key() -> None:
    """The dangerous convenience: a 31-byte key that is quietly padded into a valid one."""

    with pytest.raises(ValueError):
        ed25519.sign(_TEST_SEED[:-1], b"message")
    with pytest.raises(ValueError):
        ed25519.sign(_TEST_SEED + b"\x00", b"message")


@pytest.mark.parametrize("size", [0, 16, 31, 33, 64])
def test_public_key_from_private_rejects_a_private_key_of_the_wrong_size(size: int) -> None:
    with pytest.raises(ValueError):
        ed25519.public_key_from_private(b"\x00" * size)


@pytest.mark.parametrize("size", [0, 16, 31, 33, 64])
def test_generate_keypair_rejects_a_seed_of_the_wrong_size(size: int) -> None:
    with pytest.raises(ValueError):
        ed25519.generate_keypair(b"\x00" * size)


def test_keys_and_messages_must_be_bytes() -> None:
    with pytest.raises(ValueError):
        ed25519.generate_keypair("0" * 32)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ed25519.sign("0" * 32, b"message")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ed25519.sign(_TEST_SEED, "message")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The properties a mutation cannot express
# ---------------------------------------------------------------------------------------


def test_random_keypairs_and_messages_all_round_trip_and_each_mutation_fails() -> None:
    """A fixed seed keeps the suite reproducible; the widths vary so length is not a constant."""

    rng = random.Random(0xED25519)
    for case in range(12):
        private_key, public_key = ed25519.generate_keypair(rng.randbytes(32))
        message = rng.randbytes(rng.randrange(0, 300))
        signature = ed25519.sign(private_key, message)
        assert ed25519.verify(public_key, signature, message) is True, f"case {case}"

        mutated_signature = _flip_bit(signature, rng.randrange(8 * ed25519.SIGNATURE_SIZE))
        assert ed25519.verify(public_key, mutated_signature, message) is False, f"case {case}"
        if message:
            mutated_message = _flip_bit(message, rng.randrange(8 * len(message)))
            assert ed25519.verify(public_key, signature, mutated_message) is False, f"case {case}"


def test_a_signature_that_only_satisfies_the_cofactored_equation_is_refused() -> None:
    """Which of RFC 8032 §5.1.7's two checks this module implements, demonstrated.

    The RFC permits checking ``[8][S]B = [8]R + [8][k]A'`` instead of ``[S]B = R + [k]A'``.
    The cofactored form is weaker, and this test builds the witness: a public key of order
    dividing 8 (``[L]P`` for a point that decodes), with ``R = [S]B`` for an arbitrary S.
    Then ``[8]R = [8][S]B`` and ``[8][k]A'`` is the identity, so the cofactored equation
    holds for **every** message — while no key ever signed anything, and the cofactorless
    equation fails. Both halves are asserted, so this cannot pass by the witness being
    degenerate in some other way.
    """

    small_order_point = None
    for y in range(2, 64):
        candidate = ed25519._decode_point(y.to_bytes(32, "little"))
        if candidate is None:
            continue
        reduced = ed25519._scalar_mult(candidate, RFC_GROUP_ORDER_L)
        if ed25519._encode_point(reduced) != IDENTITY_POINT:
            small_order_point = reduced
            break
    assert small_order_point is not None, "no point of order dividing 8 found; the witness is broken"
    small_order_key = ed25519._encode_point(small_order_point)

    scalar = 0x0F1E2D3C4B5A6978
    encoded_r = ed25519._encode_point(ed25519._scalar_mult(ed25519._BASE, scalar))
    forged = encoded_r + scalar.to_bytes(32, "little")
    message = b"never signed by anybody"

    k = int.from_bytes(hashlib.sha512(encoded_r + small_order_key + message).digest(), "little") % RFC_GROUP_ORDER_L
    left = ed25519._scalar_mult(ed25519._BASE, 8 * scalar)
    right = ed25519._add(
        ed25519._scalar_mult(ed25519._decode_point(encoded_r), 8),
        ed25519._scalar_mult(small_order_point, 8 * k),
    )
    assert ed25519._encode_point(left) == ed25519._encode_point(right), (
        "the cofactored equation was expected to hold for this witness"
    )
    assert ed25519.verify(small_order_key, forged, message) is False


def test_a_degenerate_public_key_verifies_a_forgery_so_the_key_must_be_pinned() -> None:
    """The one case where ``verify`` says yes to something nobody signed — by design, not by bug.

    With the neutral element as the public key, ``[k]A`` is the identity for *every* k, so
    the group equation collapses to ``R = [S]B``: pick any S, publish ``R = [S]B``, and
    every message verifies. That is RFC 8032 behaviour (the identity is a valid curve
    point) and this module does not special-case small-order keys, so the caller has to be
    the one that decides *which* key is the issuer's. AIROOT's approval path therefore
    pins the key from the protected boundary and never reads it out of the document it is
    checking — :func:`airoot.crypto.ed25519.verify` says so in its own docstring.

    The same forgery is refused by a key that is actually somebody's, which is why this is
    a statement about key *provenance* and not about the equation being too weak.
    """

    scalar = 0x2A
    forged = ed25519._encode_point(ed25519._scalar_mult(ed25519._BASE, scalar)) + scalar.to_bytes(32, "little")
    assert ed25519._decode_point(IDENTITY_POINT) is not None, "the identity must decode, or this is a different story"
    assert ed25519.verify(IDENTITY_POINT, forged, b"never signed") is True

    _private_key, public_key = ed25519.generate_keypair(_TEST_SEED)
    assert ed25519.verify(public_key, forged, b"never signed") is False


def test_the_ed25519ctx_vector_is_refused() -> None:
    """RFC 8032 §7.2's "foo"/context-"foo" vector: same curve, **different algorithm**.

    Ed25519ctx feeds ``dom2(0, context)`` into both hashes. The key derivation is shared —
    the public key below derives correctly, which is what shows the module is right about
    the curve and the key — and the signature still must not verify, because accepting it
    would mean this module had implemented Ed25519ctx while claiming Ed25519.
    """

    secret_key = bytes.fromhex("0305334e381af78f141cb666f6199f57bc3495335a256a95bd2a55bf546663f6")
    public_key = bytes.fromhex("dfc9425e4f968f7f0c29f0259cf5f9aed6851c2bb4ad8bfb860cfee0ab248292")
    message = bytes.fromhex("f726936d19c800494e3fdaff20b276a8")
    signature = bytes.fromhex(
        "55a4cc2f70a54e04288c5f4cd1e45a7bb520b36292911876cada7323198dd87a"
        "8b36950b95130022907a7fb7c4e9b2d5f6cca685a587b4b21f4b888e4e7edb0d"
    )
    assert ed25519.public_key_from_private(secret_key) == public_key
    assert ed25519.verify(public_key, signature, message) is False


def test_the_ed25519ph_vector_is_refused() -> None:
    """RFC 8032 §7.3's "abc" vector: Ed25519ph pre-hashes, and the API has no prehash parameter."""

    secret_key = bytes.fromhex("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42")
    public_key = bytes.fromhex("ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf")
    signature = bytes.fromhex(
        "98a70222f0b8121aa9d30f813d683f809e462b469c7ff87639499bb94e6dae41"
        "31f85042463c2a355a2003d062adf5aaa10b8c61e636062aaad11c2a26083406"
    )
    assert ed25519.public_key_from_private(secret_key) == public_key
    assert ed25519.verify(public_key, signature, b"abc") is False
    # The same key, the same "abc", signed as *pure* Ed25519 by this module: a different
    # signature, which is the concrete form of "these are two algorithms".
    assert ed25519.sign(secret_key, b"abc") != signature
