"""Best-effort TvC frame-data CSV export with durable source files.

Every completed rich scan first updates a dedicated CSV for the observed
character.  The exporter then rebuilds the shared master CSV from the complete
per-character folder, while preserving any older master rows and the two manual
review columns.  The game scanner therefore never has to write a monolithic
workbook directly from a transient live result.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from tvcgui.core.paths import resource_path, user_data_path
from tvcgui.features.combat.move_filters import is_purged_move_label

EXPORT_FILE_NAME = "TvC_Frame_Data_Observed.csv"
EXPORT_DIRECTORY_NAME = "frame_data_by_character"
TEMPLATE_FILE_NAME = "TvC_Frame_Data_Observed_template.csv"
EXPORT_SCHEMA = "tvc_continuo.frame_data_observed.v1"
EXPORT_DISCLAIMER = (
    "BEST-EFFORT REVERSE-ENGINEERED FRAME DATA. This sheet is not 100% accurate; "
    "verify values with in-game testing before treating them as final."
)

# Keep the sheet deliberately flat so Excel, LibreOffice, Google Sheets, and
# CSV-aware diff tools can all read it without a Python package dependency.
CSV_FIELDS = (
    "schema",
    "accuracy_notice",
    "confidence_note",
    "character_id",
    "character",
    "profile_key",
    "last_seen_slot",
    "last_seen_utc",
    "move_kind",
    "data_origin",
    "move_label",
    "move_id_hex",
    "move_id_decimal",
    "hit_segment",
    "duplicate_index",
    "move_address",
    "active_start",
    "active_end",
    "active_frames",
    "active2_start",
    "active2_end",
    "recovery",
    "total_animation_frames",
    "hitstun",
    "blockstun",
    "hitstop",
    "advantage_on_hit",
    "advantage_on_block",
    "damage",
    "meter",
    "multi_hit_count",
    "invulnerability",
    "invulnerability_frames",
    "stun_source",
    "hitstun_source",
    "blockstun_source",
    "hitstop_source",
    "recovery_source",
    "runtime_hit_samples",
    "runtime_block_samples",
    "runtime_recovery_samples",
    "profile_source",
    "character_table_address",
    "manual_verification",
    "research_notes",
    "row_key",
)


def master_export_path() -> Path:
    """Return the persistent compiled master CSV under ``data/exports``.

    This is deliberately a runtime file, never a bundled release asset.  A new
    EXE therefore cannot overwrite an already collected master workbook simply
    by being extracted over the old release folder.
    """
    return Path(user_data_path("exports", EXPORT_FILE_NAME))


def default_export_directory() -> Path:
    """Return the persistent per-character source folder under ``data/exports``."""
    return Path(user_data_path("exports", EXPORT_DIRECTORY_NAME))


def default_export_path() -> Path:
    """Return the compiled master CSV path for compatibility callers."""
    return master_export_path()


def legacy_export_path() -> Path:
    """Compatibility alias for the established ``data/exports`` master path."""
    return master_export_path()


def prior_root_export_path() -> Path:
    """Return the short-lived older ``exports/`` master location, if present.

    Earlier patch builds used an ``exports`` folder beside the EXE.  The current
    layout returns to ``data/exports`` as requested, but this candidate is read
    once on startup so an already-collected master is never abandoned.
    """
    return Path(user_data_path()).parent / "exports" / EXPORT_FILE_NAME


def prior_root_character_directory() -> Path:
    """Return the older per-character folder used by the interim export build."""
    return Path(user_data_path()).parent / "exports" / EXPORT_DIRECTORY_NAME


def bundled_template_path() -> Path:
    """Return the optional bundled blank template, never the live master file."""
    return Path(resource_path("data", "templates", TEMPLATE_FILE_NAME))

def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (tuple, list, set)):
        return "; ".join(_as_text(v) for v in value if v not in (None, ""))
    return str(value)


def _first_text(move: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _as_text(move.get(key)).strip()
        if text:
            return text
    return ""


def _hex(value: Any) -> str:
    parsed = _as_int(value)
    return "" if parsed is None else f"0x{parsed:08X}"


def _export_label(move: Mapping[str, Any]) -> str:
    """Return a readable CSV label even for unmapped internal action rows."""
    label = _first_text(move, "pretty_name", "move_name", "name", "move", "super_name", "map_move_name")
    if label:
        return label
    kind = _as_text(move.get("kind")).strip() or "action"
    action_id = _as_int(move.get("id"))
    if action_id is not None:
        return f"{kind.title()} 0x{action_id:04X}"
    addr = _as_int(move.get("abs"))
    if addr is not None:
        return f"{kind.title()} @ 0x{addr:08X}"
    return kind.title()


def _data_origin(move: Mapping[str, Any], slot: Mapping[str, Any] | None = None) -> str:
    explicit = _as_text(move.get("_export_data_origin")).strip()
    if explicit:
        return explicit
    kind = _as_text(move.get("kind")).strip().lower()
    if "projectile" in kind:
        return "projectile profile"
    if "super" in kind or "dispatch" in kind:
        return "special/super profile"
    if slot and bool(slot.get("profile_fast_path")):
        return "saved full action profile"
    return "dynamic full action scan"


def _confidence_note(move: Mapping[str, Any]) -> str:
    origin = _data_origin(move).lower()
    if origin == "projectile profile":
        return (
            "Projectile-profile row; verify startup, repeat hits, spacing, and ownership "
            "with in-game testing before confirming."
        )
    if origin == "special/super profile":
        return (
            "Special/super dispatch row; verify that this owned script/payload maps to "
            "one player-facing move before confirming frame data."
        )
    runtime = move.get("runtime_stun")
    runtime_seen = isinstance(runtime, Mapping) and any(
        runtime.get(key) is not None
        for key in ("hitstun", "blockstun", "hitstop", "recovery")
    )
    sources = " ".join(
        _as_text(move.get(key)).lower()
        for key in ("stun_source", "hitstun_source", "blockstun_source", "hitstop_source", "recovery_source")
    )
    if runtime_seen or "runtime_observed" in sources:
        return "Includes runtime-observed values; compare repeated samples before confirming."
    if not _as_text(move.get("active_start")) or not _as_text(move.get("active_end")):
        return "Partial scanner row; active-window data is missing or unresolved."
    return "Scanner-derived row; verify with in-game tests before confirming."


def _active_frames(move: Mapping[str, Any]) -> str:
    start = _as_int(move.get("active_start"))
    end = _as_int(move.get("active_end"))
    if start is None or end is None or end < start:
        return ""
    return str(end - start + 1)


def _key_text(value: Any) -> str:
    return _as_text(value).replace("|", "/").strip()


def _row_key(slot: Mapping[str, Any], move: Mapping[str, Any]) -> str:
    """Stable logical identity for one player-facing/exported row."""
    char_id = _as_int(slot.get("char_id")) or 0
    profile_key = _key_text(slot.get("profile_key"))
    kind = _key_text(move.get("kind")).lower() or "unknown"
    action_id = _as_int(move.get("id"))
    segment = _as_int(move.get("_hit_segment_index"))
    duplicate = _as_int(move.get("dup_index"))
    label = _key_text(_export_label(move))
    if action_id is not None:
        identity = f"action:{action_id:04X}"
    else:
        raw_identity = _key_text(move.get("_export_identity"))
        if not raw_identity:
            addr = _as_int(move.get("abs"))
            raw_identity = f"addr:{addr:08X}" if addr is not None else "row"
        identity = f"raw:{raw_identity}"
    return "|".join(
        (
            str(char_id),
            profile_key,
            kind,
            identity,
            "" if segment is None else str(segment),
            "" if duplicate is None else str(duplicate),
            label,
        )
    )


def _row_key_from_csv(row: Mapping[str, Any]) -> str:
    """Migrate older address-heavy keys without losing manual sheet notes."""
    old = _as_text(row.get("row_key")).strip()
    if "|raw:" in old:
        return old
    action_id = _as_int(row.get("move_id_decimal"))
    char_id = _as_int(row.get("character_id")) or 0
    profile_key = _key_text(row.get("profile_key"))
    kind = _key_text(row.get("move_kind")).lower() or "unknown"
    segment = _as_int(row.get("hit_segment"))
    duplicate = _as_int(row.get("duplicate_index"))
    label = _key_text(row.get("move_label"))
    if action_id is not None:
        identity = f"action:{action_id:04X}"
    else:
        addr = _as_int(row.get("move_address"))
        identity = f"raw:addr:{addr:08X}" if addr is not None else "raw:row"
    return "|".join((
        str(char_id), profile_key, kind, identity,
        "" if segment is None else str(segment),
        "" if duplicate is None else str(duplicate), label,
    ))


def _projectile_export_move(hit: Mapping[str, Any], index: int) -> dict[str, Any]:
    label = _first_text(hit, "move", "move_name", "name", "label") or f"Projectile {index + 1}"
    addr = _as_int(hit.get("addr")) or _as_int(hit.get("dmg_write_addr"))
    role = _first_text(hit, "role", "cluster", "fmt", "key")
    identity = "|".join(
        part for part in (
            _key_text(label), _key_text(role),
            _key_text(hit.get("phase")), _key_text(hit.get("variant")),
            f"addr:{addr:08X}" if addr is not None else f"index:{index}",
        ) if part
    )
    return {
        "kind": "projectile emitter" if bool(hit.get("is_emitter") or hit.get("emitter")) else "projectile",
        "pretty_name": label,
        "move_name": label,
        "id": None,
        "abs": addr,
        "damage": hit.get("dmg", hit.get("damage")),
        "meter": hit.get("meter"),
        "multi_hit_count": hit.get("hit_count", hit.get("hits")),
        "_export_data_origin": "projectile profile",
        "_export_identity": identity,
    }


def _super_export_move(hit: Mapping[str, Any], index: int) -> dict[str, Any]:
    label = _first_text(hit, "super_name", "map_move_name", "move", "name")
    selector = _as_int(hit.get("selector"))
    variant = _as_int(hit.get("variant"))
    phase = _as_int(hit.get("phase"))
    dispatch_index = _as_int(hit.get("dispatch_index"))
    if not label:
        label = "Special/Super Dispatch"
    if selector is not None:
        label = f"{label} [sel 0x{selector:02X}]"
    addr = _as_int(hit.get("addr")) or _as_int(hit.get("super_entry_addr"))
    owned = hit.get("owned_field_map")
    damage = hit.get("damage")
    if damage in (None, "", "?") and isinstance(owned, Mapping):
        candidate = owned.get("damage")
        if isinstance(candidate, Mapping):
            damage = candidate.get("value")
    identity = "|".join(
        part for part in (
            _key_text(_first_text(hit, "super_name", "map_move_name", "move", "name")),
            f"sel:{selector:02X}" if selector is not None else "",
            f"var:{variant}" if variant is not None else "",
            f"phase:{phase}" if phase is not None else "",
            f"dispatch:{dispatch_index}" if dispatch_index is not None else "",
            f"addr:{addr:08X}" if addr is not None else f"index:{index}",
        ) if part
    )
    return {
        "kind": "super dispatch",
        "pretty_name": label,
        "move_name": label,
        "id": None,
        "abs": addr,
        "damage": damage,
        "multi_hit_count": hit.get("payload_count"),
        "_export_data_origin": "special/super profile",
        "_export_identity": identity,
    }


def _iter_export_moves(slot: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """Yield every frame-data family the completed rich scan has emitted."""
    moves = slot.get("moves")
    if isinstance(moves, Iterable) and not isinstance(moves, (str, bytes, Mapping)):
        for move in moves:
            if isinstance(move, Mapping):
                if is_purged_move_label(slot, dict(move)):
                    continue
                yield move
    projectile_hits = slot.get("profile_projectile_hits")
    if isinstance(projectile_hits, Iterable) and not isinstance(projectile_hits, (str, bytes, Mapping)):
        for index, hit in enumerate(projectile_hits):
            if isinstance(hit, Mapping):
                yield _projectile_export_move(hit, index)
    super_hits = slot.get("profile_super_hits")
    if isinstance(super_hits, Iterable) and not isinstance(super_hits, (str, bytes, Mapping)):
        for index, hit in enumerate(super_hits):
            if isinstance(hit, Mapping):
                yield _super_export_move(hit, index)


def _safe_file_stem(value: Any) -> str:
    """Make a stable Windows-safe character filename stem."""
    text = _as_text(value).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    return text or "Unknown"


class FrameDataSpreadsheetExporter:
    """Persist character source CSVs, then compile them into one master CSV.

    Source files live in ``data/exports/frame_data_by_character``.  The master
    remains ``data/exports/TvC_Frame_Data_Observed.csv``.  A source file is
    updated atomically before the master is rebuilt, so an incomplete live scan
    can never replace the whole workbook.
    """

    def __init__(self, *, path: Path | None = None, master_path: Path | None = None) -> None:
        requested = Path(master_path or path or master_export_path())
        # Older internal callers treated ``path`` as a directory.  Continue to
        # accept that form while making the default/master path unambiguous.
        if requested.suffix.lower() != ".csv":
            requested = requested / EXPORT_FILE_NAME
        self.path = requested
        self.character_directory = self.path.parent / EXPORT_DIRECTORY_NAME
        self._lock = threading.RLock()
        self._rows_by_path: dict[Path, dict[str, dict[str, str]]] = {}
        self._loaded_paths: set[Path] = set()
        self.last_error = ""
        self.last_write_count = 0
        self.last_character_write_count = 0
        self.last_master_row_count = 0
        self.character_directory.mkdir(parents=True, exist_ok=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_prior_export_layout_if_needed()
        # Safe on startup: no source file can remove an older master row, and
        # no disk write happens unless the merged result actually differs.
        self.compile_master_from_character_exports()

    def _migrate_prior_export_layout_if_needed(self) -> None:
        """Copy older runtime exports into the requested ``data/exports`` layout.

        Migration is deliberately one-way and only fills missing/empty targets.
        The prior master and any prior character CSVs are left untouched as an
        additional recovery copy.
        """
        try:
            old_master = prior_root_export_path()
            if self._data_row_count(self.path) == 0 and self._data_row_count(old_master) > 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_master, self.path)
            old_source_dir = prior_root_character_directory()
            if old_source_dir.is_dir():
                for source_path in old_source_dir.glob("*.csv"):
                    if source_path.name.lower().endswith(".previous.csv"):
                        continue
                    target_path = self.character_directory / source_path.name
                    if self._data_row_count(target_path) == 0 and self._data_row_count(source_path) > 0:
                        shutil.copy2(source_path, target_path)
        except Exception as exc:
            self.last_error = f"prior export migration failed: {exc!r}"

    @property
    def directory(self) -> Path:
        """Per-character source folder, retained for UI/status output."""
        return self.character_directory

    def character_path(self, slot: Mapping[str, Any]) -> Path:
        char_id = _as_int(slot.get("char_id")) or 0
        char_name = _safe_file_stem(slot.get("char_name") or slot.get("profile_key") or "Unknown")
        return self.character_directory / f"{char_id:02d}_{char_name}.csv"

    @staticmethod
    def _data_row_count(path: Path) -> int:
        try:
            if not path.is_file():
                return 0
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                return sum(1 for row in csv.DictReader(handle) if isinstance(row, dict))
        except Exception:
            return 0

    @staticmethod
    def _backup_path(path: Path) -> Path:
        return path.with_name(f"{path.stem}.previous.csv")

    def _read_rows(self, path: Path) -> dict[str, dict[str, str]]:
        """Read one CSV as normalized logical rows without mutating its file."""
        rows: dict[str, dict[str, str]] = {}
        try:
            if not path.exists():
                return rows
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if not isinstance(row, dict):
                        continue
                    key = _row_key_from_csv(row)
                    if not key:
                        continue
                    normalized = {field: _as_text(row.get(field)) for field in CSV_FIELDS}
                    normalized["row_key"] = key
                    prior = rows.get(key)
                    if prior:
                        for manual_key in ("manual_verification", "research_notes"):
                            if not normalized.get(manual_key) and prior.get(manual_key):
                                normalized[manual_key] = prior[manual_key]
                    rows[key] = normalized
        except Exception as exc:
            self.last_error = f"read failed for {path.name}: {exc!r}"
        return rows

    def _load_existing(self, path: Path) -> dict[str, dict[str, str]]:
        """Load and cache a character source file for the current process."""
        if path not in self._loaded_paths:
            self._loaded_paths.add(path)
            self._rows_by_path[path] = self._read_rows(path)
        return self._rows_by_path.setdefault(path, {})

    @staticmethod
    def _rows_equal(left: Mapping[str, Mapping[str, str]], right: Mapping[str, Mapping[str, str]]) -> bool:
        return dict(left) == dict(right)

    def _write_rows(self, path: Path, rows_by_key: Mapping[str, Mapping[str, str]]) -> bool:
        """Atomically replace one CSV after retaining a one-generation backup."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if self._data_row_count(path) > 0:
                try:
                    shutil.copy2(path, self._backup_path(path))
                except Exception as backup_exc:
                    self.last_error = f"backup failed for {path.name}: {backup_exc!r}"
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.stem + ".",
                suffix=".tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as handle:
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(sorted(
                        rows_by_key.values(),
                        key=lambda row: (
                            row.get("character", "").lower(),
                            row.get("move_kind", "").lower(),
                            _as_int(row.get("move_id_decimal")) or 0,
                            _as_int(row.get("hit_segment")) or 0,
                            row.get("move_label", "").lower(),
                        ),
                    ))
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                os.replace(tmp_name, path)
            finally:
                try:
                    if os.path.exists(tmp_name):
                        os.unlink(tmp_name)
                except OSError:
                    pass
            self.last_error = ""
            self.last_write_count = len(rows_by_key)
            return True
        except Exception as exc:
            self.last_error = f"write failed for {path.name}: {exc!r}"
            return False

    def _build_row(self, slot: Mapping[str, Any], move: Mapping[str, Any], *, seen_utc: str) -> dict[str, str]:
        action_id = _as_int(move.get("id"))
        label = _export_label(move)
        invulnerability = _first_text(move, "invuln_kind", "invuln")
        runtime = move.get("runtime_stun") if isinstance(move.get("runtime_stun"), Mapping) else {}
        origin = _data_origin(move, slot)
        return {
            "schema": EXPORT_SCHEMA,
            "accuracy_notice": EXPORT_DISCLAIMER,
            "confidence_note": _confidence_note(move),
            "character_id": _as_text(slot.get("char_id")),
            "character": _as_text(slot.get("char_name")),
            "profile_key": _as_text(slot.get("profile_key")),
            "last_seen_slot": _as_text(slot.get("slot_label") or slot.get("slot")),
            "last_seen_utc": seen_utc,
            "move_kind": _as_text(move.get("kind") or "unknown"),
            "data_origin": origin,
            "move_label": label,
            "move_id_hex": "" if action_id is None else f"0x{action_id:04X}",
            "move_id_decimal": "" if action_id is None else str(action_id),
            "hit_segment": _as_text(move.get("_hit_segment_index")),
            "duplicate_index": _as_text(move.get("dup_index")),
            "move_address": _hex(move.get("abs")),
            "active_start": _as_text(move.get("active_start")),
            "active_end": _as_text(move.get("active_end")),
            "active_frames": _active_frames(move),
            "active2_start": _as_text(move.get("active2_start")),
            "active2_end": _as_text(move.get("active2_end")),
            "recovery": _as_text(move.get("recovery")),
            "total_animation_frames": _as_text(move.get("animation_total_frames")),
            "hitstun": _as_text(move.get("hitstun")),
            "blockstun": _as_text(move.get("blockstun")),
            "hitstop": _as_text(move.get("hitstop")),
            "advantage_on_hit": _as_text(move.get("adv_hit")),
            "advantage_on_block": _as_text(move.get("adv_block")),
            "damage": _as_text(move.get("damage")),
            "meter": _as_text(move.get("meter")),
            "multi_hit_count": _as_text(move.get("multi_hit_count")),
            "invulnerability": invulnerability,
            "invulnerability_frames": _as_text(move.get("invuln_frames")),
            "stun_source": _as_text(move.get("stun_source")),
            "hitstun_source": _as_text(move.get("hitstun_source")),
            "blockstun_source": _as_text(move.get("blockstun_source")),
            "hitstop_source": _as_text(move.get("hitstop_source")),
            "recovery_source": _as_text(move.get("recovery_source")),
            "runtime_hit_samples": _as_text(runtime.get("hitstun_samples")),
            "runtime_block_samples": _as_text(runtime.get("blockstun_samples")),
            "runtime_recovery_samples": _as_text(runtime.get("recovery_samples")),
            "profile_source": origin,
            "character_table_address": _hex(slot.get("chr_tbl_abs")),
            "manual_verification": "",
            "research_notes": "",
            "row_key": _row_key(slot, move),
        }

    @staticmethod
    def _preserve_manual_columns(new_row: dict[str, str], old_row: Mapping[str, str] | None) -> dict[str, str]:
        if old_row:
            for manual_key in ("manual_verification", "research_notes"):
                if not _as_text(new_row.get(manual_key)):
                    new_row[manual_key] = _as_text(old_row.get(manual_key))
        return new_row

    def _source_csv_paths(self) -> list[Path]:
        """Return only real per-character source CSVs, never backups or master."""
        if not self.character_directory.exists():
            return []
        return sorted(
            path for path in self.character_directory.glob("*.csv")
            if path.is_file() and not path.name.lower().endswith(".previous.csv")
        )

    def compile_master_from_character_exports(self) -> bool:
        """Merge every per-character CSV into the established master workbook.

        Existing master-only rows are deliberately retained.  This is the
        fail-safe that protects the already-complete master while the source
        folder is gradually populated one character at a time.  Source values
        update matching logical rows, and manual review columns survive from
        either source or master.
        """
        with self._lock:
            existing_master = self._read_rows(self.path)
            merged: dict[str, dict[str, str]] = dict(existing_master)
            source_count = 0
            for source_path in self._source_csv_paths():
                source_rows = self._read_rows(source_path)
                if not source_rows:
                    continue
                source_count += 1
                for key, source_row in source_rows.items():
                    updated = {field: _as_text(source_row.get(field)) for field in CSV_FIELDS}
                    updated["row_key"] = key
                    merged[key] = self._preserve_manual_columns(updated, existing_master.get(key))
            self.last_master_row_count = len(merged)
            # No sources and no existing master means there is nothing to create.
            if source_count == 0 and not existing_master:
                return False
            if self.path.exists() and self._rows_equal(existing_master, merged):
                return True
            return self._write_rows(self.path, merged)

    # Simple alias for any future GUI button or test harness.
    rebuild_master = compile_master_from_character_exports

    def upsert_scan_rows(self, scan_rows: Iterable[Mapping[str, Any]] | None) -> bool:
        """Write each observed character first, then rebuild the shared master.

        A four-character rich scan updates at most four durable source files.
        Only after those writes succeed does the compiler scan the entire source
        folder and merge it into ``data/exports/TvC_Frame_Data_Observed.csv``.
        """
        with self._lock:
            seen_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            grouped: dict[Path, tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = {}
            for slot in scan_rows or ():
                if not isinstance(slot, Mapping):
                    continue
                char_id = _as_int(slot.get("char_id"))
                if char_id is None or char_id <= 0:
                    continue
                moves = list(_iter_export_moves(slot))
                if not moves:
                    continue
                char_path = self.character_path(slot)
                if char_path not in grouped:
                    grouped[char_path] = (slot, [])
                grouped[char_path][1].extend(moves)

            wrote_character_source = False
            observed_character_file = False
            self.last_character_write_count = 0
            for char_path, (slot, moves) in grouped.items():
                rows = self._load_existing(char_path)
                changed = False
                for move in moves:
                    key = _row_key(slot, move)
                    if not key or key.startswith("0|"):
                        continue
                    updated = self._build_row(slot, move, seen_utc=seen_utc)
                    updated = self._preserve_manual_columns(updated, rows.get(key))
                    if rows.get(key) != updated:
                        rows[key] = updated
                        changed = True
                if changed:
                    if self._write_rows(char_path, rows):
                        wrote_character_source = True
                        observed_character_file = True
                        self.last_character_write_count += 1
                elif char_path.exists():
                    observed_character_file = True

            if wrote_character_source:
                master_ok = self.compile_master_from_character_exports()
                if not master_ok and not self.last_error:
                    self.last_error = "master compile did not produce a CSV"
                return master_ok
            # A prior source may have been created by another run; still allow
            # a completed rich result to heal a missing master without touching
            # scanner behavior.
            if observed_character_file and not self.path.exists():
                return self.compile_master_from_character_exports()
            return observed_character_file

__all__ = [
    "EXPORT_DISCLAIMER",
    "EXPORT_DIRECTORY_NAME",
    "EXPORT_FILE_NAME",
    "TEMPLATE_FILE_NAME",
    "FrameDataSpreadsheetExporter",
    "bundled_template_path",
    "default_export_directory",
    "default_export_path",
    "legacy_export_path",
    "master_export_path",
    "prior_root_character_directory",
    "prior_root_export_path",
]
