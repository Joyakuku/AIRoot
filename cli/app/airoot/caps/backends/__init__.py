"""Install backends: fetch and commit steps only (三大核心契约 §4.4, draft §21).

Nothing in this package may change an active binding: that stays the transaction's
``ACTIVE_BOUND`` step. What lives here is *who moves bytes* and *what they declare*.
"""

from __future__ import annotations

from .base import (
    BACKEND_IDS,
    BACKEND_OPERATIONS,
    DECLARED_FIELDS,
    Artifact,
    BackendDeclaration,
    InstallBackend,
    VerifyResult,
    assert_script_free,
    declaration_for,
    resolve_backend,
    sha256_file,
)

__all__ = [
    "Artifact",
    "BACKEND_IDS",
    "BACKEND_OPERATIONS",
    "BackendDeclaration",
    "DECLARED_FIELDS",
    "InstallBackend",
    "VerifyResult",
    "assert_script_free",
    "declaration_for",
    "resolve_backend",
    "sha256_file",
]
