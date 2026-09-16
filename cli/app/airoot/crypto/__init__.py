"""Pure-Python cryptographic primitives, for the *checking* side of AIROOT.

``jsonschema`` is the only third-party runtime dependency this project allows
(``AGENTS.md`` §6), so anything the protected boundary has to **verify** must be
implemented here rather than imported. The package currently holds one primitive:

* :mod:`airoot.crypto.ed25519` — RFC 8032 Ed25519. It is the algorithm an
  ``approval-token`` names. The issuer that writes one lives in
  :mod:`airoot.tx.issuer` (a local, explicit step since ADR-0046) and the verifier
  in :mod:`airoot.tx.approval`; signing exists in this package for that step and for
  tests, never as an automatic act of the core.

Nothing in this package is a key store, a key protector, or an issuer, and nothing in
it is constant-time. The module docstring of :mod:`airoot.crypto.ed25519` says what
each of those omissions means for a caller.
"""

from __future__ import annotations
