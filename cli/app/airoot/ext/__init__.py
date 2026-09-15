"""Capability Extension protocol: manifest loading and the response envelope."""

from __future__ import annotations

from .envelope import envelope
from .fake import FakeExtension
from .manifest import (
    load_manifest,
    load_manifests,
    manifest_paths,
    register_manifests,
    require_supported_protocol,
)

__all__ = [
    "envelope",
    "FakeExtension",
    "load_manifest",
    "load_manifests",
    "manifest_paths",
    "register_manifests",
    "require_supported_protocol",
]
