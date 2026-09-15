"""Read-only PE static metadata.

Evidence level 2 of the ladder in v0.3 §9.9 ("PE/ELF 静态元数据、版本资源和架构").
This module **only reads bytes**: it never executes, loads, maps or runs the file it
inspects, and it degrades gracefully on malformed input (a corrupt header returns a
result with ``errors`` populated rather than raising).

Using a file's *name* is never sufficient to identify a capability — v0.3 §9.3:716
forbids that — so callers must combine this with the whitelist predicates.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAX_HEADER_BYTES = 64 * 1024  # DOS/COFF/optional headers + the section table
MAX_RESOURCE_SECTION_BYTES = 8 * 1024 * 1024  # the .rsrc section is where version info lives

MACHINE_ARCHITECTURE = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
    0x01C0: "arm",
    0x01C4: "arm",
}

RT_VERSION = 16
VS_FIXEDFILEINFO_SIGNATURE = 0xFEEF04BD


@dataclass
class PeMetadata:
    """What static inspection can prove about an executable."""

    path: str
    is_pe: bool = False
    architecture: str | None = None
    machine: int | None = None
    is_dll: bool = False
    subsystem: int | None = None
    file_version: str | None = None
    product_version: str | None = None
    product_name: str | None = None
    company_name: str | None = None
    file_description: str | None = None
    original_filename: str | None = None
    size_bytes: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def probe_level(self) -> int:
        return 2 if self.is_pe else 0

    def facts(self) -> dict[str, object]:
        """Non-null facts, for evidence records."""

        facts: dict[str, object] = {
            "architecture": self.architecture,
            "is_dll": self.is_dll,
        }
        for key in ("product_name", "company_name", "file_description", "original_filename",
                    "file_version", "product_version"):
            value = getattr(self, key)
            if value:
                facts[key] = value
        return {key: value for key, value in facts.items() if value not in (None, "")}


# --------------------------------------------------------------------------- #
# low level readers (all bounds-checked)
# --------------------------------------------------------------------------- #


def _u16(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(data):
        return None
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def _utf16(data: bytes, offset: int, *, limit: int | None = None) -> str:
    end = len(data)
    if limit is not None and limit > 0:
        end = min(end, offset + limit * 2)
    cursor = offset
    while cursor + 1 < end:
        if data[cursor] == 0 and data[cursor + 1] == 0:
            break
        cursor += 2
    try:
        return data[offset:cursor].decode("utf-16-le", errors="replace")
    except (UnicodeDecodeError, ValueError):  # pragma: no cover - defensive
        return ""


def _version_string(ms: int | None, ls: int | None) -> str | None:
    if ms is None or ls is None:
        return None
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


# --------------------------------------------------------------------------- #
# PE structure
# --------------------------------------------------------------------------- #


@dataclass
class _Sections:
    entries: list[tuple[int, int, int, int]]  # virtual_address, virtual_size, raw_offset, raw_size

    def rva_to_offset(self, rva: int) -> int | None:
        for virtual_address, virtual_size, raw_offset, raw_size in self.entries:
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return raw_offset + (rva - virtual_address)
        return None

    def section_for_rva(self, rva: int) -> tuple[int, int, int, int] | None:
        for entry in self.entries:
            virtual_address, virtual_size, _raw_offset, raw_size = entry
            if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                return entry
        return None


def _parse_headers(data: bytes, metadata: PeMetadata) -> tuple[int, _Sections] | None:
    if len(data) < 0x40 or data[0:2] != b"MZ":
        metadata.errors.append("not a PE image (no MZ signature)")
        return None
    pe_offset = _u32(data, 0x3C)
    if pe_offset is None or pe_offset + 24 > len(data) or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        metadata.errors.append("not a PE image (no PE signature)")
        return None

    coff = pe_offset + 4
    machine = _u16(data, coff)
    number_of_sections = _u16(data, coff + 2) or 0
    size_of_optional_header = _u16(data, coff + 16) or 0
    characteristics = _u16(data, coff + 18) or 0

    metadata.is_pe = True
    metadata.machine = machine
    metadata.architecture = MACHINE_ARCHITECTURE.get(machine or -1)
    metadata.is_dll = bool(characteristics & 0x2000)

    optional = coff + 20
    magic = _u16(data, optional)
    if magic == 0x20B:
        directory_offset = optional + 112
        metadata.subsystem = _u16(data, optional + 68)
    elif magic == 0x10B:
        directory_offset = optional + 96
        metadata.subsystem = _u16(data, optional + 68)
    else:
        metadata.errors.append("unknown optional header magic")
        return None

    resource_rva = _u32(data, directory_offset + 8 * 2)
    if resource_rva is None:
        metadata.errors.append("optional header truncated before the resource directory")
        return None

    sections_start = optional + size_of_optional_header
    entries: list[tuple[int, int, int, int]] = []
    for index in range(number_of_sections):
        base = sections_start + index * 40
        if base + 40 > len(data):
            break
        virtual_size = _u32(data, base + 8) or 0
        virtual_address = _u32(data, base + 12) or 0
        raw_size = _u32(data, base + 16) or 0
        raw_offset = _u32(data, base + 20) or 0
        entries.append((virtual_address, virtual_size, raw_offset, raw_size))

    return resource_rva, _Sections(entries)


def _find_version_resource(section: bytes, directory_offset: int, section_rva: int) -> bytes | None:
    """Locate RT_VERSION inside the resource section, working in section-relative offsets."""

    base = directory_offset
    if base < 0 or base + 16 > len(section):
        return None

    def entries_at(directory: int) -> list[tuple[int, int]]:
        named = _u16(section, directory + 12) or 0
        ids = _u16(section, directory + 14) or 0
        found: list[tuple[int, int]] = []
        for index in range(named + ids):
            entry = directory + 16 + index * 8
            name_or_id = _u32(section, entry)
            offset = _u32(section, entry + 4)
            if name_or_id is None or offset is None:
                break
            found.append((name_or_id, offset))
        return found

    # level 0: resource type -> level 1: name/id -> level 2: language -> data entry
    for name_or_id, offset in entries_at(base):
        if name_or_id != RT_VERSION or not offset & 0x80000000:
            continue
        level1 = base + (offset & 0x7FFFFFFF)
        for _name, level1_offset in entries_at(level1):
            if not level1_offset & 0x80000000:
                continue
            level2 = base + (level1_offset & 0x7FFFFFFF)
            for _lang, level2_offset in entries_at(level2):
                if level2_offset & 0x80000000:
                    continue
                data_entry = base + level2_offset
                rva = _u32(section, data_entry)
                size = _u32(section, data_entry + 4)
                if rva is None or size is None:
                    continue
                start = rva - section_rva
                if start < 0 or start + size > len(section):
                    continue
                return section[start : start + size]
    return None


@dataclass
class _Block:
    """One ``VS_VERSIONINFO`` structure: it nests inside its parent's byte range."""

    key: str
    value_length: int
    value_type: int  # 0 = binary (length in bytes), 1 = text (length in words)
    value_offset: int
    children_offset: int
    end: int


def _read_block(data: bytes, offset: int, limit: int) -> _Block | None:
    if offset < 0 or offset + 6 > limit:
        return None
    length = _u16(data, offset) or 0
    value_length = _u16(data, offset + 2) or 0
    value_type = _u16(data, offset + 4) or 0
    if length <= 0:
        return None
    end = min(limit, offset + length)

    cursor = offset + 6
    key_start = cursor
    while cursor + 1 < end and not (data[cursor] == 0 and data[cursor + 1] == 0):
        cursor += 2
    key = data[key_start:cursor].decode("utf-16-le", errors="replace")
    cursor = (cursor + 2 + 3) & ~3  # skip the NUL terminator, align to 4

    value_offset = cursor
    value_bytes = value_length if value_type == 0 else value_length * 2
    children_offset = (value_offset + value_bytes + 3) & ~3
    return _Block(key, value_length, value_type, value_offset, children_offset, end)


WANTED_STRINGS = {
    "ProductName": "product_name",
    "CompanyName": "company_name",
    "FileDescription": "file_description",
    "OriginalFilename": "original_filename",
}


def _parse_string_tables(data: bytes, node: _Block, metadata: PeMetadata) -> None:
    cursor = node.children_offset
    while cursor + 6 <= node.end:
        table = _read_block(data, cursor, node.end)
        if table is None:
            return
        inner = table.children_offset
        while inner + 6 <= table.end:
            entry = _read_block(data, inner, table.end)
            if entry is None:
                break
            if entry.value_length:
                value = _utf16(data, entry.value_offset, limit=entry.value_length)
                attribute = WANTED_STRINGS.get(entry.key)
                if attribute and value and not getattr(metadata, attribute):
                    setattr(metadata, attribute, value)
            inner = (entry.end + 3) & ~3
        cursor = (table.end + 3) & ~3


def _parse_version_info(block: bytes, metadata: PeMetadata) -> None:
    root = _read_block(block, 0, len(block))
    if root is None or root.key != "VS_VERSION_INFO":
        metadata.errors.append("VS_VERSIONINFO root block not found")
        return

    if root.value_type == 0 and root.value_length >= 52:
        if _u32(block, root.value_offset) == VS_FIXEDFILEINFO_SIGNATURE:
            metadata.file_version = _version_string(
                _u32(block, root.value_offset + 8), _u32(block, root.value_offset + 12)
            )
            metadata.product_version = _version_string(
                _u32(block, root.value_offset + 16), _u32(block, root.value_offset + 20)
            )

    cursor = root.children_offset
    while cursor + 6 <= root.end:
        child = _read_block(block, cursor, root.end)
        if child is None:
            return
        if child.key == "StringFileInfo":
            _parse_string_tables(block, child, metadata)
        cursor = (child.end + 3) & ~3


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def probe_executable(path: Path | str) -> PeMetadata:
    """Static facts about ``path``; never executes it and never reads it whole.

    Only two bounded ranges are read: the headers (``MAX_HEADER_BYTES``) and the
    resource section (``MAX_RESOURCE_SECTION_BYTES``). Reading the whole file would
    make this unusable on real binaries — a modern ``node.exe`` is well over 100 MB.
    """

    target = Path(path)
    metadata = PeMetadata(path=str(target))
    # I/O goes through the extended-length form so a deep data root is readable regardless of the
    # machine's long-path policy; the *reported* path stays native (draft §38).
    from ..paths import extended_path

    readable = extended_path(target)
    try:
        size = os.stat(readable).st_size
    except OSError as exc:
        metadata.errors.append(f"unreadable: {exc}")
        return metadata
    metadata.size_bytes = size
    if size < 64:
        metadata.errors.append(f"file is too small to be a PE image ({size} bytes)")
        return metadata

    try:
        with open(readable, "rb") as handle:
            header = handle.read(min(size, MAX_HEADER_BYTES))
            headers = _parse_headers(header, metadata)
            if headers is None:
                return metadata
            resource_rva, sections = headers

            section = sections.section_for_rva(resource_rva)
            if section is None:
                metadata.errors.append("the resource directory is not inside any section")
                return metadata
            section_rva, _virtual_size, raw_offset, raw_size = section
            if raw_size > MAX_RESOURCE_SECTION_BYTES:
                metadata.errors.append(
                    f"resource section is {raw_size} bytes, above the {MAX_RESOURCE_SECTION_BYTES} inspection cap"
                )
            handle.seek(raw_offset)
            section_bytes = handle.read(min(raw_size, MAX_RESOURCE_SECTION_BYTES))
    except OSError as exc:  # pragma: no cover - permission denied etc.
        metadata.errors.append(f"unreadable: {exc}")
        return metadata

    try:
        block = _find_version_resource(section_bytes, resource_rva - section_rva, section_rva)
        if block is not None:
            _parse_version_info(block, metadata)
        else:
            metadata.errors.append("no RT_VERSION resource")
    except (struct.error, ValueError, IndexError) as exc:  # pragma: no cover - defensive
        metadata.errors.append(f"version resource unparsable: {exc}")
    return metadata


def is_probably_executable(path: Path) -> bool:
    """Cheap pre-filter: only real PE images are worth parsing."""

    try:
        with Path(path).open("rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


__all__ = ["PeMetadata", "probe_executable", "is_probably_executable", "MACHINE_ARCHITECTURE"]
