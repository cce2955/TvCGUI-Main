# dolphin_io.py
#
# Thin wrapper around dolphin_memory_engine so we can safely
# read AND WRITE Dolphin's emulated Wii memory (MEM1 + MEM2).
#
# CHANGE (MEM2 latch):
#   Dolphin can have multiple MEM2-sized committed regions. DME can
#   attach to the wrong one. We "latch" the correct MEM2 host base by
#   validating a known sentinel EA address and expected bytes.
#
# Exposes:
#   hook()               - block until Dolphin is hooked AND MEM2 latched
#   addr_in_ram(a)       - True if address in MEM1 or MEM2
#   rbytes(addr, size)   - bulk read, returns bytes
#   rd8(addr)            - read 8-bit unsigned, returns int 0..255 or None
#   rd32(addr)           - read 32-bit BE unsigned
#   rdf32(addr)          - read 32-bit BE float
#   wd8(addr, val)       - write 8-bit unsigned
#   wd32(addr, val)      - write 32-bit BE unsigned
#   wdf32(addr, val)     - write 32-bit BE float
#   wbytes(addr, data)   - write bytes

import time
import math
import struct
import os
import sys
import ctypes
from ctypes import wintypes

import dolphin_memory_engine as dme
from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI

# ============================================================
# MEM2 LATCH CONFIG
# ============================================================


EXPECT_EA = 0x9246B9C0
EXPECT_BYTES = bytes.fromhex(
    "00 00 80 0F 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 0C 00 00 00 00 00 00 00 00"
)

# Accept the observed toggle variant if you want (0x80 -> 0x00 at byte[2]).
# If you don't want this tolerance, set EXPECT_BYTES_ALT = None.
EXPECT_BYTES_ALT = bytearray(EXPECT_BYTES)
EXPECT_BYTES_ALT[2] = 0x00
EXPECT_BYTES_ALT = bytes(EXPECT_BYTES_ALT)

MEM2_SIZE = MEM2_HI - MEM2_LO  # usually 0x04000000 (64MB)

# Process name to target
DOLPHIN_EXE = "Dolphin.exe"

# How often we retry latching if we can't find it yet
LATCH_RETRY_SLEEP = 0.25

# ============================================================
# WINDOWS PROCESS MEMORY HELPERS (ctypes)
# ============================================================

_IS_WINDOWS = (os.name == "nt")

if _IS_WINDOWS:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    PROCESS_VM_WRITE = 0x0020
    PROCESS_VM_OPERATION = 0x0008

    MEM_COMMIT = 0x1000

    PAGE_NOACCESS = 0x01
    PAGE_READONLY = 0x02
    PAGE_READWRITE = 0x04
    PAGE_WRITECOPY = 0x08
    PAGE_EXECUTE = 0x10
    PAGE_EXECUTE_READ = 0x20
    PAGE_EXECUTE_READWRITE = 0x40
    PAGE_EXECUTE_WRITECOPY = 0x80
    PAGE_GUARD = 0x100
    PAGE_NOCACHE = 0x200
    PAGE_WRITECOMBINE = 0x400

    READABLE_PROTECT = {
        PAGE_READONLY,
        PAGE_READWRITE,
        PAGE_WRITECOPY,
        PAGE_EXECUTE_READ,
        PAGE_EXECUTE_READWRITE,
        PAGE_EXECUTE_WRITECOPY,
    }

    WRITABLE_PROTECT = {
        PAGE_READWRITE,
        PAGE_WRITECOPY,
        PAGE_EXECUTE_READWRITE,
        PAGE_EXECUTE_WRITECOPY,
    }

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", wintypes.LPVOID),
            ("AllocationBase", wintypes.LPVOID),
            ("AllocationProtect", wintypes.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL

    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.LPCVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL

    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t


# ============================================================
# MEM2 LATCH STATE
# ============================================================

_mem2_proc_handle = None
_mem2_pid = None
_mem2_host_base = None
_mem2_host_size = None


def _reset_mem2_latch():
    global _mem2_proc_handle, _mem2_pid, _mem2_host_base, _mem2_host_size
    if _mem2_proc_handle is not None and _IS_WINDOWS:
        try:
            kernel32.CloseHandle(_mem2_proc_handle)
        except Exception:
            pass
    _mem2_proc_handle = None
    _mem2_pid = None
    _mem2_host_base = None
    _mem2_host_size = None


def _find_pid_by_name(exe_name: str):
    """
    Find a PID for exe_name on Windows. Tries psutil first, then tasklist.
    Returns int PID or None.
    """
    if not _IS_WINDOWS:
        return None

    # Try psutil if present
    try:
        import psutil  # type: ignore
        for p in psutil.process_iter(["pid", "name"]):
            nm = p.info.get("name")
            if nm and nm.lower() == exe_name.lower():
                return int(p.info["pid"])
    except Exception:
        pass

    # Fallback: tasklist parsing
    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV"],
            universal_newlines=True,
            stderr=subprocess.DEVNULL,
        )
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) >= 2:
            # "Image Name","PID",...
            cols = [c.strip().strip('"') for c in lines[1].split(",")]
            if len(cols) >= 2 and cols[0].lower() == exe_name.lower():
                return int(cols[1])
    except Exception:
        pass

    return None


def _open_process(pid: int):
    if not _IS_WINDOWS:
        return None
    access = (
        PROCESS_QUERY_INFORMATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
        | PROCESS_VM_OPERATION
    )
    h = kernel32.OpenProcess(access, False, pid)
    if not h:
        return None
    return h


def _rpm(hproc, addr: int, size: int) -> bytes:
    if not _IS_WINDOWS or not hproc or size <= 0:
        return b""
    buf = (ctypes.c_ubyte * size)()
    nread = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        hproc,
        ctypes.c_void_p(addr),
        buf,
        size,
        ctypes.byref(nread),
    )
    if not ok or nread.value != size:
        return b""
    return bytes(buf)


def _wpm(hproc, addr: int, data: bytes) -> bool:
    if not _IS_WINDOWS or not hproc or not data:
        return False
    size = len(data)
    cbuf = (ctypes.c_ubyte * size).from_buffer_copy(data)
    nwritten = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(
        hproc,
        ctypes.c_void_p(addr),
        cbuf,
        size,
        ctypes.byref(nwritten),
    )
    return bool(ok and nwritten.value == size)


def _iter_regions(hproc):
    """
    Yield (base, size, protect, state) for all regions in the process.
    """
    if not _IS_WINDOWS or not hproc:
        return
    mbi = MEMORY_BASIC_INFORMATION()
    addr = 0
    max_addr = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1

    while addr < max_addr:
        ret = kernel32.VirtualQueryEx(
            hproc, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
        )
        if not ret:
            break

        base = int(ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0)
        size = int(mbi.RegionSize)
        protect = int(mbi.Protect)
        state = int(mbi.State)

        yield base, size, protect, state

        # Advance
        next_addr = base + size
        if next_addr <= addr:
            break
        addr = next_addr


def _score_match(buf: bytes) -> int:
    if not buf:
        return 0
    score = 0
    for i in range(min(len(buf), len(EXPECT_BYTES))):
        if buf[i] == EXPECT_BYTES[i] or (EXPECT_BYTES_ALT and buf[i] == EXPECT_BYTES_ALT[i]):
            score += 1
    return score


def _latch_mem2():
    """
    Find the correct MEM2 host mapping by scanning large readable committed regions,
    and validating EXPECT_EA -> expected bytes.
    """
    global _mem2_proc_handle, _mem2_pid, _mem2_host_base, _mem2_host_size

    if not _IS_WINDOWS:
        return False

    pid = _find_pid_by_name(DOLPHIN_EXE)
    if pid is None:
        _reset_mem2_latch()
        return False

    # Reopen handle if PID changed
    if _mem2_pid != pid or _mem2_proc_handle is None:
        _reset_mem2_latch()
        h = _open_process(pid)
        if h is None:
            return False
        _mem2_proc_handle = h
        _mem2_pid = pid

    # Compute EA offset into MEM2
    if not (MEM2_LO <= EXPECT_EA < MEM2_HI):
        # Sentinel must be inside MEM2; if you move it, update EXPECT_EA
        return False
    ea_off = EXPECT_EA - MEM2_LO

    # Scan regions
    best = None  # (score, base, size)
    for base, size, protect, state in _iter_regions(_mem2_proc_handle):
        if state != MEM_COMMIT:
            continue

        prot = protect & 0xFF  # ignore guard/nocache flags
        if prot not in READABLE_PROTECT:
            continue
        if size < MEM2_SIZE:
            continue

        host_addr = base + ea_off
        probe = _rpm(_mem2_proc_handle, host_addr, len(EXPECT_BYTES))
        if not probe:
            continue

        score = _score_match(probe)
        if best is None or score > best[0]:
            best = (score, base, size)
            if score >= len(EXPECT_BYTES):
                # Perfect match, we can stop early.
                break

    if not best:
        _mem2_host_base = None
        _mem2_host_size = None
        return False

    score, base, size = best
    if score < 16:
        # Too weak; treat as failure
        _mem2_host_base = None
        _mem2_host_size = None
        return False

    _mem2_host_base = int(base)
    _mem2_host_size = int(size)
    return True


def _mem2_is_latched() -> bool:
    return _mem2_proc_handle is not None and _mem2_host_base is not None


def _mem2_host_addr(ea: int) -> int:
    # EA must be in MEM2 range
    return int(_mem2_host_base + (ea - MEM2_LO))


def _ensure_mem2_latched():
    """
    Ensure MEM2 latch exists. Retry a bit if Dolphin is launching.
    """
    if not _IS_WINDOWS:
        return True  # non-windows: we just fall back to DME behavior
    if _mem2_is_latched():
        return True

    # Try a few times quickly
    for _ in range(40):
        if _latch_mem2():
            return True
        time.sleep(LATCH_RETRY_SLEEP)
    return False


# ============================================================
# PUBLIC API
# ============================================================

def hook():
    """
    Block until Dolphin is successfully hooked, AND MEM2 latch is ready.
    """
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

    # MEM2 latch (Windows only). If it fails, we still return, but MEM2 reads
    # will fall back to DME (and may be wrong). So we try hard here.
    _ensure_mem2_latched()


def addr_in_ram(a):
    if a is None:
        return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)


def _clamp_read_range(addr, size):
    if size <= 0:
        return False, addr, 0

    # MEM1
    if MEM1_LO <= addr < MEM1_HI:
        hi_allowed = min(addr + size, MEM1_HI)
        return True, addr, hi_allowed - addr

    # MEM2
    if MEM2_LO <= addr < MEM2_HI:
        hi_allowed = min(addr + size, MEM2_HI)
        return True, addr, hi_allowed - addr

    return False, addr, 0


# ============================================================
# READ FUNCTIONS
# ============================================================

def rbytes(addr, size):
    ok, base, span = _clamp_read_range(addr, size)
    if not ok or span <= 0:
        return b""

    # MEM2: use latched host mapping if possible
    if _IS_WINDOWS and (MEM2_LO <= base < MEM2_HI):
        if _ensure_mem2_latched():
            host = _mem2_host_addr(base)
            data = _rpm(_mem2_proc_handle, host, span)
            if data:
                return data

        # If latch fails or RPM fails, fall back to DME (may be wrong)
        try:
            data = dme.read_bytes(base, span)
            return data if data else b""
        except Exception:
            return b""

    # MEM1 or non-windows: use DME
    try:
        data = dme.read_bytes(base, span)
        return data if data else b""
    except Exception:
        return b""


def rd8(addr):
    if not addr_in_ram(addr):
        return None
    b = rbytes(addr, 1)
    if not b or len(b) != 1:
        return None
    return b[0]


def rd32(addr):
    if not addr_in_ram(addr):
        return None
    b = rbytes(addr, 4)
    if not b or len(b) != 4:
        return None
    return struct.unpack(">I", b)[0]


def rdf32(addr):
    if not addr_in_ram(addr):
        return None
    b = rbytes(addr, 4)
    if not b or len(b) != 4:
        return None

    raw_u32 = struct.unpack(">I", b)[0]
    f = struct.unpack(">f", struct.pack(">I", raw_u32))[0]
    if not math.isfinite(f) or abs(f) > 1e8:
        return None
    return f


# ============================================================
# WRITE FUNCTIONS
# ============================================================

def wbytes(addr, data):
    if not addr_in_ram(addr):
        return False
    if not data:
        return False

    # MEM2: latched write if possible
    if _IS_WINDOWS and (MEM2_LO <= addr < MEM2_HI):
        if _ensure_mem2_latched():
            host = _mem2_host_addr(addr)
            if _wpm(_mem2_proc_handle, host, bytes(data)):
                return True

        # fallback
        try:
            dme.write_bytes(addr, data)
            return True
        except Exception as e:
            print(f"wbytes failed at {addr:08X}: {e}")
            return False

    # MEM1 or non-windows
    try:
        dme.write_bytes(addr, data)
        return True
    except Exception as e:
        print(f"wbytes failed at {addr:08X}: {e}")
        return False


def wd8(addr, value):
    try:
        val = int(value) & 0xFF
    except Exception:
        return False
    return wbytes(addr, bytes([val]))


def wd32(addr, value):
    try:
        val = int(value) & 0xFFFFFFFF
    except Exception:
        return False
    return wbytes(addr, struct.pack(">I", val))


def wdf32(addr, value):
    try:
        f = float(value)
        if not math.isfinite(f):
            return False
    except Exception:
        return False
    raw = struct.unpack(">I", struct.pack(">f", f))[0]
    return wbytes(addr, struct.pack(">I", raw))


# Optional: expose latch state for debugging
def mem2_latch_info():
    return {
        "pid": _mem2_pid,
        "host_base": _mem2_host_base,
        "host_size": _mem2_host_size,
        "latched": _mem2_is_latched(),
        "expect_ea": EXPECT_EA,
    }
