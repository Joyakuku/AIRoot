"""Registry package: SQLite authoritative store plus its JSON projection."""

from __future__ import annotations

from .db import Registry
from .entities import (
    Binding,
    DataRoot,
    ExternalReference,
    Instance,
    binding_from_row,
    binding_key,
    data_root_from_row,
    external_reference_from_row,
    instance_from_row,
)
from .projection import build_projection, projection_generation, read_projection

__all__ = [
    "Registry",
    "Binding",
    "DataRoot",
    "ExternalReference",
    "Instance",
    "binding_from_row",
    "binding_key",
    "data_root_from_row",
    "external_reference_from_row",
    "instance_from_row",
    "build_projection",
    "projection_generation",
    "read_projection",
]
