"""AIROOT CLI Core (P1, protocol level).

P1 is deliberately protocol-level: it drives the declared state machine, the
SQLite registry, the JSON projection and the deterministic ``where`` selection
without touching any real external software, ACL or machine PATH.

Authority for every wire shape is ``AIROOT\\cli\\schema``; see
``docs/AIROOT-v0.3-实现决策记录.md`` (ADR-0001) for the Python-now/Rust-later
language route.
"""

from __future__ import annotations

from pathlib import Path

PROTOCOL_VERSION = 1
SCHEMA_VERSION = 1

# cli/app/airoot/__init__.py -> cli/
CLI_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = CLI_ROOT / "schema"
EXTENSIONS_DIR = CLI_ROOT / "extensions"

# Root-relative layout owned by the core (docs, "Skill 自包含目录布局").
STATE_DIR = "state"
TX_DIR = "tx"
STORE_DIR = "store"
TOOLS_DIR = "tools"
ENV_DIR = "env"
EXPOSURE_DIR = "exposure"
CACHE_DIR = "cache"
LOGS_DIR = "logs"
QUARANTINE_DIR = "quarantine"

REGISTRY_DB = "state/registry.db"
REGISTRY_JSON = "state/registry.json"
ROOT_MARKER = "state/root.json"

__all__ = [
    "PROTOCOL_VERSION",
    "SCHEMA_VERSION",
    "CLI_ROOT",
    "SCHEMA_DIR",
    "EXTENSIONS_DIR",
    "STATE_DIR",
    "TX_DIR",
    "STORE_DIR",
    "TOOLS_DIR",
    "ENV_DIR",
    "EXPOSURE_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "QUARANTINE_DIR",
    "REGISTRY_DB",
    "REGISTRY_JSON",
    "ROOT_MARKER",
]
