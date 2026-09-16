"""Pure-Python cryptographic primitives, for the *checking* side of AIROOT.

``jsonschema`` is the only third-party runtime dependency this project allows
(``AGENTS.md`` §6), so anything the protected boundary has to **verify** must be
implemented here rather than imported. The package currently holds one primitive:

* :mod:`airoot.crypto.ed25519` — RFC 8032 Ed25519. It is the algorithm an
  ``approval-token`` names for the production issuer (``tx/approval.py``, ADR-0025
  D1), and today it is the half that can be built without a protected key store:
  verification runs on public data, signing exists for tests only.

Nothing in this package is a key store, a key protector, or an issuer, and nothing in
it is constant-time. The module docstring of :mod:`airoot.crypto.ed25519` says what
each of those omissions means for a caller.
"""

from __future__ import annotations
