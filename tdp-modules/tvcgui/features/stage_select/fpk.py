"""FPK inspection for the Tatsunoko vs. Capcom stage-select menu.

This module recognizes the real stage-select packages by their internal
filenames, decodes the compressed stage sequence, and exposes a guarded
experimental three-Wasteland clone installer for the exact verified US menu
build.  Archive writes occur only through that explicit installer.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from hashlib import sha1
from pathlib import Path
from typing import Any

_FPK_HEADER_SIZE = 0x10
_FPK_ENTRY_SIZE = 0x30
_NAME_SIZE = 0x20
_STAGE_SEQUENCE = "menu/main/stgmove.seq"
_STAGE_LAYOUT = "/main/stgmove/stgmove_us_2d.arc"


class FpkFormatError(ValueError):
    """Raised when a file cannot be read as the expected FPK container."""


class PrsFormatError(ValueError):
    """Raised when a PRS stream is malformed or ends before its expected size."""


@dataclass(frozen=True)
class FpkEntry:
    name: str
    data_offset: int
    compressed_size: int
    uncompressed_size: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "data_offset": f"0x{self.data_offset:08X}",
            "compressed_size": self.compressed_size,
            "uncompressed_size": self.uncompressed_size,
        }


def _be_u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big", signed=False)


def _safe_name(raw: bytes) -> str:
    value = raw.split(b"\0", 1)[0]
    return value.decode("ascii", errors="replace").replace("\\", "/")


def _read_fpk(path: str | Path) -> tuple[Path, bytes, list[FpkEntry]]:
    archive = Path(path)
    data = archive.read_bytes()
    if len(data) < _FPK_HEADER_SIZE:
        raise FpkFormatError("File is too small to contain an FPK header.")

    count = _be_u32(data, 0x04)
    directory_offset = _be_u32(data, 0x08)
    declared_size = _be_u32(data, 0x0C)
    if count <= 0 or count > 8192:
        raise FpkFormatError(f"Invalid FPK entry count: {count}.")
    if directory_offset < _FPK_HEADER_SIZE:
        raise FpkFormatError("Invalid FPK directory offset.")
    directory_end = directory_offset + count * _FPK_ENTRY_SIZE
    if directory_end > len(data):
        raise FpkFormatError("FPK directory runs past the end of the file.")
    if declared_size and declared_size > len(data):
        raise FpkFormatError("FPK header declares data past the end of the file.")

    entries: list[FpkEntry] = []
    for index in range(count):
        offset = directory_offset + index * _FPK_ENTRY_SIZE
        name = _safe_name(data[offset : offset + _NAME_SIZE])
        data_offset = _be_u32(data, offset + 0x24)
        compressed_size = _be_u32(data, offset + 0x28)
        uncompressed_size = _be_u32(data, offset + 0x2C)
        if not name:
            raise FpkFormatError(f"Entry {index} has no filename.")
        if data_offset + compressed_size > len(data):
            raise FpkFormatError(f"Entry {index} data runs past the end of the file.")
        entries.append(FpkEntry(name, data_offset, compressed_size, uncompressed_size))
    return archive, data, entries


def read_fpk_directory(path: str | Path) -> list[FpkEntry]:
    """Read and validate an FPK directory without modifying the archive."""
    return _read_fpk(path)[2]


def _prs_decompress(data: bytes, expected_size: int) -> bytes:
    """Decode the Eighting PRS variant used by this archive.

    This variant has no usable end marker for FPK entry decoding.  A zero long
    copy is valid and the directory's uncompressed size is therefore the stop
    condition.
    """
    if expected_size < 0:
        raise PrsFormatError("Invalid expected PRS output size.")
    if expected_size == 0:
        return b""

    source = 0
    out = bytearray()
    control = 0
    bit_index = 8

    def take_bit() -> int:
        nonlocal source, control, bit_index
        if bit_index >= 8:
            if source >= len(data):
                raise PrsFormatError("PRS control stream ended early.")
            control = data[source]
            source += 1
            bit_index = 0
        value = (control >> (7 - bit_index)) & 1
        bit_index += 1
        return value

    while len(out) < expected_size:
        if take_bit():
            if source >= len(data):
                raise PrsFormatError("PRS literal stream ended early.")
            out.append(data[source])
            source += 1
            continue

        if take_bit():
            if source + 2 > len(data):
                raise PrsFormatError("PRS long-copy stream ended early.")
            word = (data[source] << 8) | data[source + 1]
            source += 2
            count = word & 0x07
            offset = (word >> 3) | ~0x1FFF
            if count == 0:
                if source >= len(data):
                    raise PrsFormatError("PRS extended-copy stream ended early.")
                count = data[source] + 1
                source += 1
            else:
                count += 2
        else:
            count = (take_bit() << 1) | take_bit()
            count += 2
            if source >= len(data):
                raise PrsFormatError("PRS short-copy stream ended early.")
            offset = data[source] - 0x100
            source += 1

        start = len(out) + offset
        if start < 0:
            raise PrsFormatError("PRS copy references bytes before the output buffer.")
        for index in range(count):
            if len(out) >= expected_size:
                break
            try:
                out.append(out[start + index])
            except IndexError as exc:
                raise PrsFormatError("PRS copy references bytes past the output buffer.") from exc

    return bytes(out)


def _entry_bytes(data: bytes, entry: FpkEntry) -> bytes:
    return data[entry.data_offset : entry.data_offset + entry.compressed_size]


def _stage_menu_details(data: bytes, entries: list[FpkEntry]) -> dict[str, Any] | None:
    by_name = {entry.name.lower(): entry for entry in entries}
    sequence = by_name.get(_STAGE_SEQUENCE)
    layout = by_name.get(_STAGE_LAYOUT)
    if sequence is None or layout is None:
        return None

    sequence_bytes = _entry_bytes(data, sequence)
    try:
        decoded = _prs_decompress(sequence_bytes, sequence.uncompressed_size)
    except PrsFormatError as exc:
        return {
            "verified": False,
            "reason": f"Found both stage assets, but stgmove.seq could not be decoded: {exc}",
        }

    # The stock sequence's stage descriptor run begins with these paired tags.
    # Each full descriptor is 0x38 bytes.  The last stock descriptor transitions
    # directly into the finish script, so we report the count rather than pretend
    # that three free descriptors already exist.
    table_marker = b"stg01\0\0\0stg01\0\0\0"
    table_offset = decoded.find(table_marker)
    descriptor_tags: list[str] = []
    if table_offset >= 0:
        for index in range(32):
            offset = table_offset + index * 0x38
            tag = decoded[offset : offset + 5]
            if len(tag) < 5 or tag[:3] != b"stg" or not tag[3:5].isdigit():
                break
            descriptor_tags.append(tag.decode("ascii"))

    texture_tags: list[str] = []
    for stage in range(1, 16):
        token = f"stg{stage:02d}_".encode("ascii")
        found = decoded.find(token)
        if found >= 0:
            end = decoded.find(b"\0", found)
            if end >= 0:
                texture_tags.append(decoded[found:end].decode("ascii", errors="replace"))

    signature_source = sequence_bytes + _entry_bytes(data, layout)
    clone_installed = _is_stage_clone_sequence(decoded) if "_is_stage_clone_sequence" in globals() else False
    stock_clone_ready = (
        len(decoded) == _STAGE_SEQ_EXPECTED_SIZE
        and _be_u32(decoded, _STAGE_STG14_NEXT_PTR) == _STAGE_STG14_NEXT_EXPECTED
        and decoded[_STAGE_WASTELAND_NODE : _STAGE_WASTELAND_NODE + 5] == b"stg03"
    ) if "_STAGE_SEQ_EXPECTED_SIZE" in globals() else False
    if clone_installed:
        append_reason = "Three experimental Wasteland clone nodes are installed after stg14."
    elif stock_clone_ready:
        append_reason = (
            "The stock chain is eligible for the guarded experimental extension: three exact stg03 nodes "
            "are appended and stg14 is relinked to them before the original terminal node."
        )
    else:
        append_reason = "This sequence does not match the exact stock or known clone layout supported by the installer."
    return {
        "verified": True,
        "reason": "Contains the real stage-select sequence and its paired 2D layout archive.",
        "sequence": {
            "name": sequence.name,
            "compressed_size": sequence.compressed_size,
            "uncompressed_size": sequence.uncompressed_size,
            "sha1": sha1(sequence_bytes).hexdigest()[:16],
        },
        "layout": {
            "name": layout.name,
            "compressed_size": layout.compressed_size,
            "uncompressed_size": layout.uncompressed_size,
            "sha1": sha1(_entry_bytes(data, layout)).hexdigest()[:16],
        },
        "package_signature": sha1(signature_source).hexdigest()[:16],
        "stage_descriptor_offset": f"0x{table_offset:08X}" if table_offset >= 0 else "--",
        "stock_stage_descriptor_count": len(descriptor_tags),
        "stock_stage_descriptors": descriptor_tags,
        "stock_texture_tags": texture_tags,
        "wasteland_asset": "stg03_was.tpl" if b"stg03_was.tpl" in decoded else "--",
        "append_ready": stock_clone_ready,
        "clone_installed": clone_installed,
        "append_reason": append_reason,
    }


def classify_archive(entries: list[FpkEntry], *, data: bytes | None = None) -> tuple[str, int, str]:
    """Return a conservative role label, score, and patch-safety reason."""
    names = [entry.name.lower() for entry in entries]
    joined = "\n".join(names)

    if _STAGE_SEQUENCE in names and _STAGE_LAYOUT in names:
        return (
            "Verified stage-select menu package",
            250,
            "Contains menu/main/stgmove.seq and the paired stgmove_us_2d.arc layout.",
        )

    if any(name == "menu/main/title.seq" for name in names):
        return (
            "Title/main loader",
            0,
            "Contains menu/main/title.seq but no stage selector sequence. Do not patch it for stage rows.",
        )

    stage_terms = ("stage", "stg", "stgsel", "stgmove")
    stage_entries = [name for name in names if any(term in name for term in stage_terms)]
    menu_entries = [name for name in names if name.startswith("menu/")]
    seq_entries = [name for name in names if name.endswith(".seq")]

    if stage_entries:
        return (
            "Potential stage-menu archive",
            100 + len(stage_entries),
            "Contains stage-related internal entries. It still needs a row-layout check before patching.",
        )
    if menu_entries and seq_entries:
        return (
            "Possible menu archive",
            20 + len(seq_entries),
            "Contains menu sequence files but no explicit stage selector name.",
        )
    if "menu/" in joined:
        return (
            "Menu-adjacent archive",
            5,
            "Contains menu assets but no identifiable stage selector sequence.",
        )
    return (
        "Unrelated archive",
        0,
        "No stage or menu sequence entries were found.",
    )


def inspect_fpk(path: str | Path) -> dict[str, Any]:
    """Produce a compact, UI-safe summary for one archive."""
    archive = Path(path)
    try:
        _, data, entries = _read_fpk(archive)
        classification, score, reason = classify_archive(entries, data=data)
        stage_menu = _stage_menu_details(data, entries)
    except (OSError, FpkFormatError) as exc:
        return {
            "ok": False,
            "path": str(archive),
            "classification": "Unreadable",
            "score": 0,
            "reason": str(exc),
            "entries": [],
            "stage_menu": {},
        }
    return {
        "ok": True,
        "path": str(archive),
        "classification": classification,
        "score": score,
        "reason": reason,
        "entries": [entry.as_dict() for entry in entries],
        "stage_menu": stage_menu or {},
    }


def scan_for_stage_archives(root: str | Path, *, limit: int = 4000) -> dict[str, Any]:
    """Inspect FPK directories below ``root`` and group real stage packages.

    No game files are decompressed to disk, changed, copied, or backed up by
    this scan.  The sequence is decoded only in memory when an archive has both
    known stage-select internal entries.
    """
    base = Path(root)
    if not base.is_dir():
        return {
            "ok": False,
            "root": str(base),
            "scanned": 0,
            "truncated": False,
            "candidates": [],
            "stage_sets": [],
            "reason": "Choose the extracted game folder, not an individual file.",
        }

    scanned = 0
    candidates: list[dict[str, Any]] = []
    stage_sets_by_signature: dict[str, dict[str, Any]] = {}
    truncated = False
    try:
        for archive in base.rglob("*.fpk"):
            if scanned >= limit:
                truncated = True
                break
            scanned += 1
            info = inspect_fpk(archive)
            if not info.get("ok") or int(info.get("score") or 0) <= 0:
                continue
            entry_names = [str(item.get("name") or "") for item in info.get("entries") or []]
            candidate = {
                "path": str(archive),
                "classification": str(info.get("classification") or "Unknown"),
                "score": int(info.get("score") or 0),
                "reason": str(info.get("reason") or ""),
                "entries": entry_names[:12],
                "stage_menu": info.get("stage_menu") or {},
            }
            candidates.append(candidate)

            details = candidate["stage_menu"]
            signature = str(details.get("package_signature") or "")
            if details.get("verified") and signature:
                group = stage_sets_by_signature.setdefault(
                    signature,
                    {
                        "signature": signature,
                        "paths": [],
                        "stage_descriptor_offset": details.get("stage_descriptor_offset", "--"),
                        "stock_stage_descriptor_count": details.get("stock_stage_descriptor_count", 0),
                        "wasteland_asset": details.get("wasteland_asset", "--"),
                        "append_ready": bool(details.get("append_ready")),
                        "append_reason": details.get("append_reason", ""),
                    },
                )
                group["paths"].append(str(archive))
    except OSError as exc:
        return {
            "ok": False,
            "root": str(base),
            "scanned": scanned,
            "truncated": truncated,
            "candidates": [],
            "stage_sets": [],
            "reason": str(exc),
        }

    candidates.sort(key=lambda row: (-int(row["score"]), row["path"].lower()))
    stage_sets = sorted(stage_sets_by_signature.values(), key=lambda row: (row["paths"][0].lower(), row["signature"]))
    for group in stage_sets:
        group["paths"].sort(key=str.lower)
        group["package_count"] = len(group["paths"])

    return {
        "ok": True,
        "root": str(base),
        "scanned": scanned,
        "truncated": truncated,
        "candidates": candidates[:80],
        "stage_sets": stage_sets,
        "reason": "",
    }

# --- Experimental Wasteland clone installer ---------------------------------
#
# These constants are derived from the verified US stage menu sequence above.
# The menu list is a linked script: stg14 points to stg15's terminal action.
# The installer appends three exact stg03 (Wasteland) node blocks, then changes
# that one link so the stock chain becomes stg14 -> clone 1 -> clone 2 -> clone
# 3 -> stg15.  The clones retain Wasteland's stage value (0x04) and resource
# name (stg03), so no new gameplay stage or 2D layout pane is invented.
_STAGE_SEQ_EXPECTED_SIZE = 0x582E0
_STAGE_WASTELAND_NODE = 0x8374
_STAGE_NODE_SIZE = 0x38
_STAGE_STG14_NEXT_PTR = 0x8608
_STAGE_STG14_NEXT_EXPECTED = 0x862C
_STAGE_STG15_ACTION = 0x862C
_STAGE_CLONE_COUNT = 3
_STAGE_BACKUP_SUFFIX = ".stageclone.bak"


class StageClonePatchError(ValueError):
    """Raised when an archive is not the exact menu build this patch supports."""


def _align(value: int, boundary: int = 0x20) -> int:
    return (int(value) + boundary - 1) & ~(boundary - 1)


def _literal_prs_compress(payload: bytes) -> bytes:
    """Create a valid PRS stream using literal runs only.

    It is deliberately larger than the original compressed stream, but it is
    deterministic and avoids guessing at Eighting's match finder.  The FPK
    repacker adjusts every following data offset accordingly.
    """
    if not payload:
        return b""
    out = bytearray()
    for start in range(0, len(payload), 8):
        chunk = payload[start : start + 8]
        count = len(chunk)
        control = ((1 << count) - 1) << (8 - count)
        out.append(control)
        out.extend(chunk)
    return bytes(out)


def _is_stage_clone_sequence(decoded: bytes) -> bool:
    """Return True only for this installer's exact three-node extension."""
    if len(decoded) != _STAGE_SEQ_EXPECTED_SIZE + _STAGE_NODE_SIZE * _STAGE_CLONE_COUNT:
        return False
    if _be_u32(decoded, _STAGE_STG14_NEXT_PTR) != _STAGE_SEQ_EXPECTED_SIZE + 0x18:
        return False
    template = decoded[_STAGE_WASTELAND_NODE : _STAGE_WASTELAND_NODE + _STAGE_NODE_SIZE]
    for index in range(_STAGE_CLONE_COUNT):
        start = _STAGE_SEQ_EXPECTED_SIZE + index * _STAGE_NODE_SIZE
        node = bytearray(decoded[start : start + _STAGE_NODE_SIZE])
        expected_next = (
            _STAGE_SEQ_EXPECTED_SIZE + (index + 1) * _STAGE_NODE_SIZE + 0x18
            if index + 1 < _STAGE_CLONE_COUNT
            else _STAGE_STG15_ACTION
        )
        if len(node) != _STAGE_NODE_SIZE or _be_u32(node, 0x2C) != expected_next:
            return False
        node[0x2C : 0x30] = template[0x2C : 0x30]
        if bytes(node) != template:
            return False
    return True


def _append_wasteland_clone_nodes(decoded: bytes) -> bytes:
    """Build the exact linked-list extension used by the experimental patch."""
    if len(decoded) != _STAGE_SEQ_EXPECTED_SIZE:
        raise StageClonePatchError(
            f"Unexpected stgmove.seq size {len(decoded):#x}; expected {_STAGE_SEQ_EXPECTED_SIZE:#x}."
        )
    if _be_u32(decoded, _STAGE_STG14_NEXT_PTR) != _STAGE_STG14_NEXT_EXPECTED:
        raise StageClonePatchError("stg14 does not point to the expected stock terminal node.")
    template = decoded[_STAGE_WASTELAND_NODE : _STAGE_WASTELAND_NODE + _STAGE_NODE_SIZE]
    if len(template) != _STAGE_NODE_SIZE or template[:5] != b"stg03" or template[0x27] != 0x04:
        raise StageClonePatchError("The stock Wasteland node no longer matches the expected signature.")

    out = bytearray(decoded)
    first_action = len(out) + 0x18
    out[_STAGE_STG14_NEXT_PTR : _STAGE_STG14_NEXT_PTR + 4] = first_action.to_bytes(4, "big")
    for index in range(_STAGE_CLONE_COUNT):
        node = bytearray(template)
        next_action = (
            len(decoded) + (index + 1) * _STAGE_NODE_SIZE + 0x18
            if index + 1 < _STAGE_CLONE_COUNT
            else _STAGE_STG15_ACTION
        )
        node[0x2C : 0x30] = next_action.to_bytes(4, "big")
        out.extend(node)
    return bytes(out)


def _repack_fpk_with_entry(path: Path, entry_name: str, replacement: bytes) -> bytes:
    """Rebuild one FPK with a replacement compressed entry payload."""
    archive, original, entries = _read_fpk(path)
    target_index = next((index for index, entry in enumerate(entries) if entry.name == entry_name), -1)
    if target_index < 0:
        raise StageClonePatchError(f"{entry_name} is not present in {archive.name}.")

    directory_offset = _be_u32(original, 0x08)
    directory_end = directory_offset + len(entries) * _FPK_ENTRY_SIZE
    cursor = _align(directory_end)
    out = bytearray(original[:cursor])
    payloads: list[bytes] = []
    positions: list[int] = []
    for index, entry in enumerate(entries):
        payload = replacement if index == target_index else _entry_bytes(original, entry)
        cursor = _align(len(out))
        if len(out) < cursor:
            out.extend(b"\0" * (cursor - len(out)))
        positions.append(cursor)
        payloads.append(payload)
        out.extend(payload)

    for index, entry in enumerate(entries):
        offset = directory_offset + index * _FPK_ENTRY_SIZE
        out[offset + 0x24 : offset + 0x28] = positions[index].to_bytes(4, "big")
        out[offset + 0x28 : offset + 0x2C] = len(payloads[index]).to_bytes(4, "big")
        if index == target_index:
            # The directory's size field is the decompressed sequence size.
            out[offset + 0x2C : offset + 0x30] = (_STAGE_SEQ_EXPECTED_SIZE + _STAGE_NODE_SIZE * _STAGE_CLONE_COUNT).to_bytes(4, "big")

    checksum = sum(sum(payload) for payload in payloads) & 0xFFFF
    out[0:4] = checksum.to_bytes(4, "big")
    out[0x0C:0x10] = len(out).to_bytes(4, "big")
    return bytes(out)


def _read_stage_sequence(path: str | Path) -> tuple[Path, bytes, list[FpkEntry], FpkEntry, bytes]:
    archive, data, entries = _read_fpk(path)
    by_name = {entry.name.lower(): entry for entry in entries}
    sequence = by_name.get(_STAGE_SEQUENCE)
    layout = by_name.get(_STAGE_LAYOUT)
    if sequence is None or layout is None:
        raise StageClonePatchError("This archive is not a verified stgmove package.")
    decoded = _prs_decompress(_entry_bytes(data, sequence), sequence.uncompressed_size)
    return archive, data, entries, sequence, decoded


def stage_clone_status(path: str | Path) -> dict[str, Any]:
    """Inspect whether one verified stage menu archive is stock or already patched."""
    archive = Path(path)
    try:
        _, _, _, _, decoded = _read_stage_sequence(archive)
    except (OSError, FpkFormatError, PrsFormatError, StageClonePatchError) as exc:
        return {"ok": False, "path": str(archive), "status": "unavailable", "reason": str(exc)}
    if _is_stage_clone_sequence(decoded):
        return {
            "ok": True,
            "path": str(archive),
            "status": "installed",
            "reason": "Three linked Wasteland clone nodes are present.",
        }
    if len(decoded) == _STAGE_SEQ_EXPECTED_SIZE and _be_u32(decoded, _STAGE_STG14_NEXT_PTR) == _STAGE_STG14_NEXT_EXPECTED:
        return {
            "ok": True,
            "path": str(archive),
            "status": "stock",
            "reason": "Verified stock stage sequence is ready for the experimental clone install.",
        }
    return {
        "ok": True,
        "path": str(archive),
        "status": "unsupported",
        "reason": "The stage sequence differs from the verified stock or known patched layout.",
    }


def install_wasteland_clones(path: str | Path) -> dict[str, Any]:
    """Install three experimental Wasteland rows into one exact stage FPK.

    A sibling ``.stageclone.bak`` is made before the first write.  The archive
    is rewritten atomically only after the rebuilt sequence decodes and verifies
    in memory.
    """
    archive = Path(path)
    status = stage_clone_status(archive)
    if not status.get("ok"):
        return status
    if status.get("status") == "installed":
        return {**status, "changed": False, "backup": str(archive) + _STAGE_BACKUP_SUFFIX}
    if status.get("status") != "stock":
        return {**status, "changed": False}

    try:
        _, _, _, _, decoded = _read_stage_sequence(archive)
        patched_sequence = _append_wasteland_clone_nodes(decoded)
        compressed_sequence = _literal_prs_compress(patched_sequence)
        rebuilt = _repack_fpk_with_entry(archive, _STAGE_SEQUENCE, compressed_sequence)

        # Validate the new FPK from the in-memory bytes before changing disk.
        temp_path = archive.with_name(archive.name + ".stageclone.verify")
        try:
            temp_path.write_bytes(rebuilt)
            _, _, verify_entries, verify_sequence, verify_decoded = _read_stage_sequence(temp_path)
            if verify_sequence.uncompressed_size != len(patched_sequence) or not _is_stage_clone_sequence(verify_decoded):
                raise StageClonePatchError("Rebuilt archive did not pass the post-write sequence check.")
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass

        backup = Path(str(archive) + _STAGE_BACKUP_SUFFIX)
        if not backup.exists():
            backup.write_bytes(archive.read_bytes())
        temp_write = archive.with_name(archive.name + ".stageclone.tmp")
        try:
            temp_write.write_bytes(rebuilt)
            os.replace(temp_write, archive)
        finally:
            try:
                temp_write.unlink()
            except OSError:
                pass
        return {
            "ok": True,
            "path": str(archive),
            "status": "installed",
            "changed": True,
            "backup": str(backup),
            "reason": "Installed three linked Wasteland clone nodes; test this only from a copied game folder.",
        }
    except (OSError, FpkFormatError, PrsFormatError, StageClonePatchError) as exc:
        return {"ok": False, "path": str(archive), "status": "failed", "changed": False, "reason": str(exc)}


def restore_wasteland_clones(path: str | Path) -> dict[str, Any]:
    """Restore one archive from the installer-created sibling backup."""
    archive = Path(path)
    backup = Path(str(archive) + _STAGE_BACKUP_SUFFIX)
    if not backup.is_file():
        return {"ok": False, "path": str(archive), "status": "no_backup", "changed": False, "reason": "No .stageclone.bak file exists for this archive."}
    try:
        # Make sure this is a plausible FPK before replacing the active copy.
        _read_fpk(backup)
        temp_write = archive.with_name(archive.name + ".stageclone.restore.tmp")
        try:
            temp_write.write_bytes(backup.read_bytes())
            os.replace(temp_write, archive)
        finally:
            try:
                temp_write.unlink()
            except OSError:
                pass
        return {
            "ok": True,
            "path": str(archive),
            "status": "restored",
            "changed": True,
            "backup": str(backup),
            "reason": "Restored the original stage archive from its stageclone backup.",
        }
    except (OSError, FpkFormatError) as exc:
        return {"ok": False, "path": str(archive), "status": "failed", "changed": False, "reason": str(exc)}


def _stage_clone_targets(root: str | Path) -> list[Path]:
    """Return all verified stage-menu FPKs below the chosen game root."""
    scan = scan_for_stage_archives(root)
    if not scan.get("ok"):
        raise StageClonePatchError(str(scan.get("reason") or "Stage archive scan failed."))
    targets: list[Path] = []
    for candidate in scan.get("candidates") or []:
        if candidate.get("classification") == "Verified stage-select menu package":
            targets.append(Path(str(candidate.get("path") or "")))
    if not targets:
        raise StageClonePatchError("No verified stage-select packages were found below this folder.")
    return sorted(set(targets), key=lambda item: str(item).lower())


def install_wasteland_clones_in_folder(root: str | Path) -> dict[str, Any]:
    """Install the exact same three-node extension into every verified menu variant."""
    try:
        targets = _stage_clone_targets(root)
    except StageClonePatchError as exc:
        return {"ok": False, "root": str(root), "results": [], "reason": str(exc)}
    results = [install_wasteland_clones(target) for target in targets]
    return {
        "ok": all(bool(row.get("ok")) for row in results),
        "root": str(root),
        "results": results,
        "reason": "",
    }


def restore_wasteland_clones_in_folder(root: str | Path) -> dict[str, Any]:
    """Restore every installer-created stage archive backup below a game folder."""
    base = Path(root)
    if not base.is_dir():
        return {"ok": False, "root": str(base), "results": [], "reason": "Choose the extracted game folder."}
    targets = sorted(
        (Path(str(backup)[: -len(_STAGE_BACKUP_SUFFIX)]) for backup in base.rglob(f"*.fpk{_STAGE_BACKUP_SUFFIX}")),
        key=lambda item: str(item).lower(),
    )
    if not targets:
        return {"ok": False, "root": str(base), "results": [], "reason": "No stageclone backups were found below this folder."}
    results = [restore_wasteland_clones(target) for target in targets]
    return {
        "ok": all(bool(row.get("ok")) for row in results),
        "root": str(base),
        "results": results,
        "reason": "",
    }
