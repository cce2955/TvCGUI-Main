"""PowerPC register capture through Dolphin's supported GDB stub.

Dolphin Memory Engine exposes emulated RAM only. Dolphin's GDB stub is the
supported external interface for the emulated Gekko and Broadway register
state. The stub accepts one client when a game boots, so TvC Continuo keeps a
single persistent connection for the life of the emulation session.
"""
from __future__ import annotations

import atexit
import base64
import configparser
import hashlib
import json
import math
import os
import select
import socket
import struct
import tempfile
import threading
import time
from datetime import datetime
from typing import Any

_GDB_DEFAULT_PORT = 2828
_GDB_CONNECT_TIMEOUT_SEC = 0.25
_GDB_IO_TIMEOUT_SEC = 2.0
_GDB_RETRY_SEC = 0.25
_GDB_MONITOR_SEC = 0.50
_AUTO_CONFIG = os.environ.get("TVC_DOLPHIN_GDB_AUTO_CONFIG", "1").strip().lower() not in {
    "0", "false", "off", "no"
}
_RESTORE_RECORD = os.path.join(tempfile.gettempdir(), "tvccontinuo_dolphin_gdb_restore.json")

_SPECIAL_REGISTERS: dict[int, str] = {
    64: "pc",
    65: "msr",
    66: "cr",
    67: "lr",
    68: "ctr",
    69: "xer",
    70: "fpscr",
    87: "pvr",
    104: "sdr1",
    105: "asr",
    106: "dar",
    107: "dsisr",
    108: "sprg0",
    109: "sprg1",
    110: "sprg2",
    111: "sprg3",
    112: "srr0",
    113: "srr1",
    114: "tbl",
    115: "tbu",
    116: "dec",
    117: "dabr",
    118: "ear",
    119: "hid0",
    120: "hid1",
    121: "iabr",
    122: "dabr_alt",
    124: "ummcr0",
    125: "upmc1",
    126: "upmc2",
    127: "usia",
    128: "ummcr1",
    129: "upmc3",
    130: "upmc4",
    131: "mmcr0",
    132: "pmc1",
    133: "pmc2",
    134: "sia",
    135: "mmcr1",
    136: "pmc3",
    137: "pmc4",
    138: "l2cr",
    139: "ictc",
    140: "thrm1",
    141: "thrm2",
    142: "thrm3",
}

_BAT_NAMES = (
    "ibat0u", "ibat0l", "ibat1u", "ibat1l",
    "ibat2u", "ibat2l", "ibat3u", "ibat3l",
    "dbat0u", "dbat0l", "dbat1u", "dbat1l",
    "dbat2u", "dbat2l", "dbat3u", "dbat3l",
)


def _now_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _hex32(value: int) -> str:
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


def _hex64(value: int) -> str:
    return f"0x{int(value) & 0xFFFFFFFFFFFFFFFF:016X}"


def _checksum(payload: bytes) -> bytes:
    return f"{sum(payload) & 0xFF:02x}".encode("ascii")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    parts: list[bytes] = []
    left = int(size)
    while left > 0:
        block = sock.recv(left)
        if not block:
            raise ConnectionError("Dolphin GDB stub closed the connection")
        parts.append(block)
        left -= len(block)
    return b"".join(parts)


def _recv_gdb_reply(sock: socket.socket) -> bytes:
    while True:
        lead = _recv_exact(sock, 1)
        if lead in {b"+", b"-"}:
            continue
        if lead == b"$":
            break

    payload = bytearray()
    while True:
        ch = _recv_exact(sock, 1)
        if ch == b"#":
            break
        payload.extend(ch)
        if len(payload) > 65536:
            raise ValueError("Dolphin GDB reply exceeded safety limit")

    received_sum = _recv_exact(sock, 2).lower()
    expected_sum = _checksum(bytes(payload)).lower()
    if received_sum != expected_sum:
        try:
            sock.sendall(b"-")
        finally:
            raise ValueError(
                f"Dolphin GDB checksum mismatch, expected {expected_sum!r}, got {received_sum!r}"
            )
    sock.sendall(b"+")
    return bytes(payload)


def _send_packet(sock: socket.socket, command: str, *, expect_reply: bool) -> str:
    payload = command.encode("ascii")
    packet = b"$" + payload + b"#" + _checksum(payload)
    sock.sendall(packet)

    ack = _recv_exact(sock, 1)
    if ack == b"-":
        sock.sendall(packet)
        ack = _recv_exact(sock, 1)
    if ack != b"+":
        raise ConnectionError(f"Unexpected Dolphin GDB acknowledgement: {ack!r}")
    if not expect_reply:
        return ""
    return _recv_gdb_reply(sock).decode("ascii", errors="replace")


def _gdb_command(sock: socket.socket, command: str) -> str:
    return _send_packet(sock, command, expect_reply=True)


def _gdb_pipeline(sock: socket.socket, commands: list[str]) -> list[str]:
    """Send read commands together and collect replies in the same order.

    Dolphin does not wait for the client acknowledgement after sending a reply.
    Queueing every read packet at once avoids one delayed TCP acknowledgement per
    register and keeps the CPU stopped for the entire register set.
    """
    if not commands:
        return []
    packets = []
    for command in commands:
        payload = command.encode("ascii")
        packets.append(b"$" + payload + b"#" + _checksum(payload))
    sock.sendall(b"".join(packets))

    replies: list[str] = []
    for command in commands:
        ack = _recv_exact(sock, 1)
        if ack == b"-":
            raise ConnectionError(f"Dolphin GDB rejected pipelined command {command!r}")
        if ack != b"+":
            raise ConnectionError(
                f"Unexpected Dolphin GDB acknowledgement for {command!r}: {ack!r}"
            )
        replies.append(_recv_gdb_reply(sock).decode("ascii", errors="replace"))
    return replies


def _gdb_continue(sock: socket.socket) -> None:
    _send_packet(sock, "c", expect_reply=False)


def _gdb_interrupt(sock: socket.socket) -> str:
    sock.sendall(b"\x03")
    return _recv_gdb_reply(sock).decode("ascii", errors="replace")


def _candidate_dolphin_ini_paths() -> list[str]:
    paths: list[str] = []
    appdata = os.environ.get("APPDATA")
    localappdata = os.environ.get("LOCALAPPDATA")
    userprofile = os.environ.get("USERPROFILE")
    documents = os.path.join(userprofile, "Documents") if userprofile else ""

    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name", "exe", "cmdline"]):
            name = str(proc.info.get("name") or "").lower()
            exe = str(proc.info.get("exe") or "")
            if name not in {"dolphin.exe", "dolphinqt2.exe", "dolphinwx.exe"}:
                continue

            cmdline = [str(value) for value in (proc.info.get("cmdline") or [])]
            for index, token in enumerate(cmdline):
                lower = token.lower()
                user_dir = ""
                if lower in {"-u", "--user", "--user-dir"} and index + 1 < len(cmdline):
                    user_dir = cmdline[index + 1]
                elif lower.startswith("--user=") or lower.startswith("--user-dir="):
                    user_dir = token.split("=", 1)[1]
                if user_dir:
                    paths.append(os.path.join(user_dir, "Config", "Dolphin.ini"))

            if exe:
                exe_dir = os.path.dirname(os.path.abspath(exe))
                paths.append(os.path.join(exe_dir, "User", "Config", "Dolphin.ini"))
                paths.append(os.path.join(exe_dir, "Config", "Dolphin.ini"))
    except Exception:
        pass

    if documents:
        paths.append(os.path.join(documents, "Dolphin Emulator", "Config", "Dolphin.ini"))
    if appdata:
        paths.append(os.path.join(appdata, "Dolphin Emulator", "Config", "Dolphin.ini"))
    if localappdata:
        paths.append(os.path.join(localappdata, "Dolphin Emulator", "Config", "Dolphin.ini"))

    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(path)
    return unique


def _read_gdb_port(path: str) -> int | None:
    if not os.path.isfile(path):
        return None
    try:
        parser = configparser.ConfigParser(strict=False)
        parser.read(path, encoding="utf-8-sig")
        for section in parser.sections():
            if section.lower() != "general":
                continue
            for key, value in parser.items(section):
                if key.lower() == "gdbport":
                    return int(str(value).strip(), 0)
    except Exception:
        return None
    return -1


def _configured_gdb_ports() -> list[int]:
    ports: list[int] = []
    raw_env = str(os.environ.get("TVC_DOLPHIN_GDB_PORT", "")).strip()
    if raw_env:
        for token in raw_env.replace(";", ",").split(","):
            try:
                port = int(token.strip(), 0)
            except Exception:
                continue
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)

    for path in _candidate_dolphin_ini_paths():
        port = _read_gdb_port(path)
        if port is not None and 1 <= port <= 65535 and port not in ports:
            ports.append(port)

    if _GDB_DEFAULT_PORT not in ports:
        ports.append(_GDB_DEFAULT_PORT)
    return ports


def _patch_gdb_port_text(text: str, port: int) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    general_start: int | None = None
    general_end = len(lines)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("[") and stripped.endswith("]")):
            continue
        section = stripped[1:-1].strip().lower()
        if general_start is not None:
            general_end = index
            break
        if section == "general":
            general_start = index

    replacement = f"GDBPort = {int(port)}{newline}"
    if general_start is None:
        if text and not text.endswith(("\n", "\r")):
            text += newline
        return text + f"[General]{newline}" + replacement

    for index in range(general_start + 1, general_end):
        raw = lines[index]
        stripped = raw.lstrip()
        if not stripped or stripped.startswith(("#", ";")) or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip().lower()
        if key == "gdbport":
            lines[index] = replacement
            return "".join(lines)

    lines.insert(general_end, replacement)
    return "".join(lines)


def _write_restore_record(path: str, original: bytes, patched: bytes) -> None:
    record = {
        "path": os.path.abspath(path),
        "original_b64": base64.b64encode(original).decode("ascii"),
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
        "created_at": _now_stamp(),
    }
    tmp = _RESTORE_RECORD + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        json.dump(record, out, indent=2)
    os.replace(tmp, _RESTORE_RECORD)


def _restore_temporary_config() -> dict[str, Any]:
    result: dict[str, Any] = {"restored": False, "record": _RESTORE_RECORD}
    if not os.path.isfile(_RESTORE_RECORD):
        return result
    try:
        with open(_RESTORE_RECORD, "r", encoding="utf-8") as src:
            record = json.load(src)
        path = str(record.get("path") or "")
        original = base64.b64decode(str(record.get("original_b64") or ""))
        expected = str(record.get("patched_sha256") or "")
        if not path or not os.path.isfile(path):
            result["error"] = "Dolphin.ini disappeared before restoration"
            return result
        with open(path, "rb") as src:
            current = src.read()
        if expected and hashlib.sha256(current).hexdigest() != expected:
            result["error"] = "Dolphin.ini changed after TvC Continuo armed GDB, so it was not overwritten"
            return result
        tmp = path + ".tvcgdb.tmp"
        with open(tmp, "wb") as out:
            out.write(original)
        os.replace(tmp, path)
        result.update({"restored": True, "path": path})
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        return result
    finally:
        try:
            os.remove(_RESTORE_RECORD)
        except Exception:
            pass


def _arm_temporary_gdb_config() -> dict[str, Any]:
    if not _AUTO_CONFIG:
        return {"armed": False, "disabled": True}

    if os.path.isfile(_RESTORE_RECORD):
        existing = {"armed": True, "record": _RESTORE_RECORD, "existing": True}
        try:
            with open(_RESTORE_RECORD, "r", encoding="utf-8") as src:
                existing.update(json.load(src))
        except Exception:
            pass
        return existing

    candidates = _candidate_dolphin_ini_paths()
    existing_paths = [path for path in candidates if os.path.isfile(path)]
    if not existing_paths:
        return {"armed": False, "error": "No Dolphin.ini was found"}

    for path in existing_paths:
        current_port = _read_gdb_port(path)
        if current_port is not None and current_port > 0:
            return {"armed": False, "already_configured": True, "path": path, "port": current_port}

    path = existing_paths[0]
    try:
        with open(path, "rb") as src:
            original = src.read()
        text = original.decode("utf-8-sig")
        patched_text = _patch_gdb_port_text(text, _GDB_DEFAULT_PORT)
        bom = b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b""
        patched = bom + patched_text.encode("utf-8")
        tmp = path + ".tvcgdb.tmp"
        with open(tmp, "wb") as out:
            out.write(patched)
        os.replace(tmp, path)
        _write_restore_record(path, original, patched)
        return {
            "armed": True,
            "path": path,
            "port": _GDB_DEFAULT_PORT,
            "restart_required": True,
            "record": _RESTORE_RECORD,
        }
    except Exception as exc:
        return {"armed": False, "path": path, "error": repr(exc)}


class _PersistentGDBClient:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._socket_lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._port: int | None = None
        self._connected_at: str | None = None
        self._last_error = ""
        self._setup: dict[str, Any] = {}
        self._restore_result: dict[str, Any] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="TvCDolphinGDB",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._socket_lock:
            sock = self._sock
            self._sock = None
            self._port = None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._restore_result = _restore_temporary_config()

    def _clear_socket(self, sock: socket.socket | None = None) -> None:
        with self._socket_lock:
            if sock is not None and self._sock is not sock:
                return
            current = self._sock
            self._sock = None
            self._port = None
            self._connected_at = None
        if current is not None:
            try:
                current.close()
            except Exception:
                pass

    def _connect_once(self) -> bool:
        for port in _configured_gdb_ports():
            if self._stop.is_set():
                return False
            try:
                sock = socket.create_connection(
                    ("127.0.0.1", int(port)), timeout=_GDB_CONNECT_TIMEOUT_SEC
                )
                sock.settimeout(_GDB_IO_TIMEOUT_SEC)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
                _gdb_continue(sock)
                with self._socket_lock:
                    self._sock = sock
                    self._port = int(port)
                    self._connected_at = _now_stamp()
                    self._last_error = ""
                self._restore_result = _restore_temporary_config()
                print(f"[gdb] connected to Dolphin on port {port}; emulation continued", flush=True)
                return True
            except Exception as exc:
                self._last_error = f"port {port}: {exc!r}"
                try:
                    sock.close()  # type: ignore[possibly-undefined]
                except Exception:
                    pass
        return False

    def _socket_closed(self, sock: socket.socket) -> bool:
        try:
            readable, _, _ = select.select([sock], [], [], _GDB_MONITOR_SEC)
            if not readable:
                return False
            peek = sock.recv(1, socket.MSG_PEEK)
            return peek == b""
        except (BlockingIOError, TimeoutError):
            return False
        except Exception:
            return True

    def _run(self) -> None:
        stale_restore = _restore_temporary_config()
        if stale_restore.get("restored"):
            print(f"[gdb] restored stale Dolphin config: {stale_restore.get('path')}", flush=True)

        while not self._stop.is_set():
            with self._socket_lock:
                sock = self._sock
            if sock is None:
                self._setup = _arm_temporary_gdb_config()
                if self._connect_once():
                    continue
                self._stop.wait(_GDB_RETRY_SEC)
                continue

            if self._socket_closed(sock):
                print("[gdb] Dolphin GDB session closed; rearming for the next boot", flush=True)
                self._clear_socket(sock)
                continue

    def status(self) -> dict[str, Any]:
        with self._socket_lock:
            connected = self._sock is not None
            port = self._port
        return {
            "connected": connected,
            "port": port,
            "connected_at": self._connected_at,
            "last_error": self._last_error,
            "setup": dict(self._setup),
            "restore": dict(self._restore_result),
            "auto_config": _AUTO_CONFIG,
        }

    def capture(self) -> dict[str, Any]:
        errors: list[str] = []
        with self._socket_lock:
            sock = self._sock
            port = self._port
            if sock is None:
                status = self.status()
                setup = status.get("setup") or {}
                return {
                    "available": False,
                    "source": "dolphin_gdb_stub",
                    "captured_at": _now_stamp(),
                    "errors": [
                        status.get("last_error") or "Dolphin GDB client is waiting for a game boot"
                    ],
                    "service": status,
                    "restart_required": bool(setup.get("restart_required", False)),
                    "hint": (
                        "Keep TvC Continuo open and restart the game once. Dolphin reads GDBPort "
                        "when emulation boots, then TvC Continuo connects and resumes it automatically."
                    ),
                }

            paused = False
            try:
                stop_reply = _gdb_interrupt(sock)
                paused = True

                commands = ["g"]
                commands.extend(f"p{index:x}" for index in range(32, 64))
                commands.extend(f"p{reg_id:x}" for reg_id in _SPECIAL_REGISTERS)
                commands.extend(f"p{index:x}" for index in range(71, 87))
                commands.extend(f"p{index:x}" for index in range(88, 104))
                replies = _gdb_pipeline(sock, commands)

                reply_index = 0
                g_reply = replies[reply_index].strip()
                reply_index += 1
                if len(g_reply) < 32 * 8 or g_reply.startswith("E"):
                    raise ValueError(f"invalid GPR packet length {len(g_reply)}")

                gpr: dict[str, str] = {}
                for index in range(32):
                    offset = index * 8
                    gpr[f"r{index}"] = _hex32(int(g_reply[offset:offset + 8], 16))

                fpr: dict[str, dict[str, Any]] = {}
                for index in range(32):
                    reply = replies[reply_index].strip()
                    reply_index += 1
                    raw, bits = _decode_register_reply(reply, 32 + index)
                    if bits != 64:
                        raise ValueError(f"f{index} was not 64-bit")
                    try:
                        as_double = struct.unpack(">d", struct.pack(">Q", raw))[0]
                        double_value: float | None = as_double if math.isfinite(as_double) else None
                    except Exception:
                        double_value = None
                    fpr[f"f{index}"] = {"raw": _hex64(raw), "double": double_value}

                special: dict[str, str] = {}
                for reg_id, name in _SPECIAL_REGISTERS.items():
                    reply = replies[reply_index].strip()
                    reply_index += 1
                    try:
                        raw, bits = _decode_register_reply(reply, reg_id)
                    except Exception as exc:
                        errors.append(f"{name}: {exc}")
                        continue
                    special[name] = _hex64(raw) if bits == 64 else _hex32(raw)

                segment: dict[str, str] = {}
                for index in range(16):
                    reply = replies[reply_index].strip()
                    reply_index += 1
                    raw, _bits = _decode_register_reply(reply, 71 + index)
                    segment[f"sr{index}"] = _hex32(raw)

                bat: dict[str, str] = {}
                for index, name in enumerate(_BAT_NAMES):
                    reply = replies[reply_index].strip()
                    reply_index += 1
                    raw, _bits = _decode_register_reply(reply, 88 + index)
                    bat[name] = _hex32(raw)

                return {
                    "available": True,
                    "source": "dolphin_gdb_stub",
                    "captured_at": _now_stamp(),
                    "gdb_port": int(port or 0),
                    "stop_reply": stop_reply,
                    "gpr": gpr,
                    "fpr": fpr,
                    "special": special,
                    "segment_registers": segment,
                    "bat_registers": bat,
                    "errors": errors,
                    "partial": bool(errors),
                    "service": self.status(),
                    "consistency_note": (
                        "Dolphin was paused for the register snapshot and resumed immediately afterward."
                    ),
                }
            except Exception as exc:
                self._last_error = repr(exc)
                self._clear_socket(sock)
                return {
                    "available": False,
                    "source": "dolphin_gdb_stub",
                    "captured_at": _now_stamp(),
                    "errors": [repr(exc)],
                    "service": self.status(),
                }
            finally:
                if paused:
                    try:
                        _gdb_continue(sock)
                    except Exception:
                        self._clear_socket(sock)


def _decode_register_reply(reply: str, reg_id: int) -> tuple[int, int]:
    reply = str(reply or "").strip()
    if not reply or reply.startswith("E"):
        raise ValueError(f"register {reg_id} unavailable: {reply or 'empty reply'}")
    bits = len(reply) * 4
    if bits not in {32, 64}:
        raise ValueError(f"register {reg_id} returned {bits} bits")
    return int(reply, 16), bits


def _read_gdb_register(sock: socket.socket, reg_id: int) -> tuple[int, int]:
    return _decode_register_reply(_gdb_command(sock, f"p{int(reg_id):x}"), reg_id)


_SERVICE = _PersistentGDBClient()
_SERVICE_STARTED = False
_SERVICE_START_LOCK = threading.Lock()


def start_ppc_register_service() -> None:
    global _SERVICE_STARTED
    with _SERVICE_START_LOCK:
        if _SERVICE_STARTED:
            return
        _SERVICE_STARTED = True
        _SERVICE.start()


def ppc_register_service_status() -> dict[str, Any]:
    start_ppc_register_service()
    return _SERVICE.status()


def capture_ppc_registers() -> dict[str, Any]:
    """Capture a coherent emulated PowerPC register snapshot."""
    start_ppc_register_service()
    started = time.time()
    snapshot = _SERVICE.capture()
    snapshot["duration_sec"] = round(time.time() - started, 4)
    return snapshot


def registers_json_bytes(snapshot: dict[str, Any]) -> bytes:
    return json.dumps(snapshot, indent=2, sort_keys=False).encode("utf-8")


atexit.register(_SERVICE.stop)

__all__ = [
    "capture_ppc_registers",
    "ppc_register_service_status",
    "registers_json_bytes",
    "start_ppc_register_service",
]
