from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MISSIONS_DIR = "missions"
MISSION_PROGRESS_FILE = "mission_progress.json"


@dataclass
class MissionStep:
    labels: List[str]


@dataclass
class MissionDef:
    mission_id: str
    name: str
    character: str
    steps: List[MissionStep] = field(default_factory=list)
    notes: str = ""
    setup_debug_flags: Dict[str, int] = field(default_factory=dict)


@dataclass
class MissionPack:
    character: str
    missions: List[MissionDef] = field(default_factory=list)


def _safe_slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _missions_path_for_character(character_name: str) -> str:
    slug = _safe_slug(character_name)
    return os.path.join(MISSIONS_DIR, f"{slug}.json")


def _load_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json_file(path: str, payload: Any) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_mission_pack(character_name: str) -> MissionPack:
    path = _missions_path_for_character(character_name)
    raw = _load_json_file(path, default=None)

    if not isinstance(raw, dict):
        return MissionPack(character=character_name, missions=[])

    missions: List[MissionDef] = []
    raw_missions = raw.get("missions", [])

    for entry in raw_missions:
        if not isinstance(entry, dict):
            continue

        mission_id = str(entry.get("mission_id", "")).strip()
        name = str(entry.get("name", mission_id)).strip()
        notes = str(entry.get("notes", "")).strip()

        setup_debug_flags: Dict[str, int] = {}
        raw_setup_debug_flags = entry.get("setup_debug_flags", {})
        if isinstance(raw_setup_debug_flags, dict):
            for key, value in raw_setup_debug_flags.items():
                try:
                    setup_debug_flags[str(key)] = int(value)
                except Exception:
                    pass

        steps: List[MissionStep] = []
        for step in entry.get("steps", []):
            if not isinstance(step, dict):
                continue

            labels: List[str] = []
            raw_labels = step.get("labels")

            if isinstance(raw_labels, list):
                for item in raw_labels:
                    text = str(item).strip()
                    if text:
                        labels.append(text)

            if not labels:
                label = str(step.get("label", "")).strip()
                if label:
                    labels.append(label)

            if not labels:
                continue

            steps.append(MissionStep(labels=labels))

        if not mission_id or not steps:
            continue

        missions.append(
            MissionDef(
                mission_id=mission_id,
                name=name,
                character=character_name,
                steps=steps,
                notes=notes,
                setup_debug_flags=setup_debug_flags,
            )
        )

    return MissionPack(character=character_name, missions=missions)


def load_progress() -> Dict[str, Any]:
    data = _load_json_file(MISSION_PROGRESS_FILE, default={})
    if not isinstance(data, dict):
        return {}
    return data


def save_progress(progress: Dict[str, Any]) -> None:
    _save_json_file(MISSION_PROGRESS_FILE, progress)


def _get_char_progress_node(progress: Dict[str, Any], character_name: str) -> Dict[str, Any]:
    char_key = _safe_slug(character_name)
    node = progress.get(char_key)

    if not isinstance(node, dict):
        node = {}

    completed = node.get("completed")
    if not isinstance(completed, dict):
        legacy_completed = {
            k: v for k, v in node.items()
            if isinstance(k, str) and k != "selected_mission_id"
        }
        completed = legacy_completed

    return {
        "completed": completed,
        "selected_mission_id": node.get("selected_mission_id"),
    }


def _set_char_progress_node(progress: Dict[str, Any], character_name: str, node: Dict[str, Any]) -> Dict[str, Any]:
    char_key = _safe_slug(character_name)
    progress[char_key] = {
        "completed": dict(node.get("completed", {})),
        "selected_mission_id": node.get("selected_mission_id"),
    }
    return progress


def is_mission_complete(progress: Dict[str, Any], character_name: str, mission_id: str) -> bool:
    node = _get_char_progress_node(progress, character_name)
    completed = node.get("completed", {})
    return bool(completed.get(mission_id, False))


def mark_mission_complete(progress: Dict[str, Any], character_name: str, mission_id: str) -> Dict[str, Any]:
    node = _get_char_progress_node(progress, character_name)
    completed = dict(node.get("completed", {}))
    completed[mission_id] = True
    node["completed"] = completed
    return _set_char_progress_node(progress, character_name, node)


def get_selected_mission_id(progress: Dict[str, Any], character_name: str) -> Optional[str]:
    node = _get_char_progress_node(progress, character_name)
    mission_id = node.get("selected_mission_id")
    return str(mission_id).strip() if mission_id else None


def set_selected_mission_id(progress: Dict[str, Any], character_name: str, mission_id: Optional[str]) -> Dict[str, Any]:
    node = _get_char_progress_node(progress, character_name)
    node["selected_mission_id"] = mission_id
    return _set_char_progress_node(progress, character_name, node)


def find_mission_by_id(pack: MissionPack, mission_id: Optional[str]) -> Optional[MissionDef]:
    if not mission_id:
        return None
    for mission in pack.missions:
        if mission.mission_id == mission_id:
            return mission
    return None


def pick_default_mission(pack: MissionPack, progress: Dict[str, Any]) -> Optional[MissionDef]:
    if not pack.missions:
        return None

    selected_id = get_selected_mission_id(progress, pack.character)
    selected = find_mission_by_id(pack, selected_id)
    if selected is not None:
        return selected

    for mission in pack.missions:
        if not is_mission_complete(progress, pack.character, mission.mission_id):
            return mission

    return pack.missions[0]


def build_overlay_payload(character_name: str) -> Dict[str, Any]:
    pack = load_mission_pack(character_name)
    progress = load_progress()
    active = pick_default_mission(pack, progress)
    selected_id = get_selected_mission_id(progress, pack.character)

    return {
        "character": pack.character,
        "mission_count": len(pack.missions),
        "active_mission_id": active.mission_id if active else None,
        "active_mission_name": active.name if active else None,
        "active_mission_notes": active.notes if active else "",
        "active_mission_steps": [step.labels for step in active.steps] if active else [],
        "active_mission_setup_debug_flags": dict(active.setup_debug_flags) if active else {},
        "selected_mission_id": selected_id,
        "missions": [
            {
                "mission_id": mission.mission_id,
                "name": mission.name,
                "notes": mission.notes,
                "completed": is_mission_complete(progress, pack.character, mission.mission_id),
                "selected": mission.mission_id == selected_id,
                "steps": [step.labels for step in mission.steps],
                "setup_debug_flags": dict(mission.setup_debug_flags),
            }
            for mission in pack.missions
        ],
    }