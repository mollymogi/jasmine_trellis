"""Binding files for the hook runtime (ADR 0017 section 10, ADR 0019).

A binding names the Core, the ownership (project/task/step/host) and the
private key files. It never contains a token. Ownership comes only from here:
neither the model nor tool arguments can select a Task.

Two forms exist. A *full* binding (`<project>/.jasmine/host-binding.json`) holds everything and
is what project-level hook registration uses. With a *device* binding (one per machine, named
by the globally registered hook command) the project file shrinks to ownership data only
(`project_id`, `task_id`, `step_id`, `inject_context`); the Core address, host id, key files
and failure policy come from the device file and a project file may not set them.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import ids

BINDING_RELATIVE = Path(".jasmine") / "host-binding.json"
ENV_BINDING = "JASMINE_HOST_BINDING"
LOOPBACK_URL = re.compile(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):[0-9]{1,5}\Z")
#: The only non-loopback Core origin a device binding may name. Not an env var.
TAILNET_CORE_ORIGIN = "https://mercuryliumac-mini.tail07df76.ts.net"
ALLOWED = {"version", "core_url", "host_id", "project_id", "task_id", "step_id", "api_key_file",
           "human_api_key_file", "on_unavailable", "spool_dir", "timeout_seconds", "inject_context"}
#: What a project file may say when a device binding applies (everything else is device-owned).
PROJECT_FIELDS = {"version", "project_id", "task_id", "step_id", "inject_context"}
DEVICE_FIELDS = {"version", "core_url", "host_id", "api_key_file", "human_api_key_file", "on_unavailable",
                 "spool_dir", "timeout_seconds", "inject_context",
                 # ADR 0036: the prompt-time Memory hint is a device setting with
                 # its own read-only credential; a project file cannot turn it on.
                 "memory_key_file", "memory_hint"}
MAX_FILE_BYTES = 64 * 1024


class BindingError(Exception):
    """The binding or a key file is unusable. Message is safe to print (no secrets)."""


@dataclass(frozen=True)
class Binding:
    path: Path
    core_url: str
    host_id: str
    project_id: str | None
    task_id: str | None
    step_id: str | None
    api_key_file: Path
    human_api_key_file: Path | None
    on_unavailable: str
    spool_dir: Path
    timeout_seconds: float | None
    inject_context: bool = True
    #: Set when the device binding supplied the device-owned fields (ADR 0019).
    device_path: Path | None = None
    #: ADR 0036: `memory:recall`-only key and the per-Agent opt-in.
    memory_key_file: Path | None = None
    memory_hint: bool = False

    def key_file_for(self, event: str) -> Path:
        if event == "prompt.submit" and self.human_api_key_file is not None:
            return self.human_api_key_file
        return self.api_key_file


def find_binding(explicit: str | os.PathLike | None, environ: dict[str, str], cwd: str | None) -> Path | None:
    """`--binding`, else JASMINE_HOST_BINDING, else upward search from cwd."""
    chosen = explicit or environ.get(ENV_BINDING)
    if chosen:
        path = Path(chosen)
        return path if path.is_file() else None
    if cwd and os.path.isabs(cwd):
        current = Path(cwd)
        for directory in (current, *current.parents):
            candidate = directory / BINDING_RELATIVE
            if candidate.is_file():
                return candidate
    return None


#: NT AUTHORITY\SYSTEM. The only trustee allowed besides the current user.
NT_AUTHORITY_SYSTEM_SID = "S-1-5-18"


def windows_dacl_allows(*, owner_sid: str, user_sid: str,
                        trustees: tuple[str, ...] | list[str] | None) -> bool:
    """Whether a Windows key-file DACL is only the current user and SYSTEM.

    ``trustees`` is every DACL ACE SID, inherited or not, ALLOW or DENY.
    ``None`` is a NULL DACL (Everyone). An explicit DENY is not required.
    This does not read the filesystem.
    """
    if not isinstance(owner_sid, str) or not isinstance(user_sid, str) or not user_sid:
        return False
    if owner_sid.upper() != user_sid.upper():
        return False
    if trustees is None:
        return False
    allowed = {user_sid.upper(), NT_AUTHORITY_SYSTEM_SID}
    return all(isinstance(sid, str) and sid.upper() in allowed for sid in trustees)


def _private_bytes(path: Path, what: str, *, limit: int = MAX_FILE_BYTES) -> bytes:
    if os.name == "nt":
        return _private_bytes_windows(path, what, limit)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise BindingError(f"{what} is not readable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BindingError(f"{what} must be a regular file owned by this user with mode 0600")
        if info.st_size > limit:
            raise BindingError(f"{what} is too large")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(descriptor)


def _private_bytes_windows(path: Path, what: str, limit: int) -> bytes:
    """Owner is the current user; every DACL trustee is that user or SYSTEM."""
    try:
        kernel32, handle, regular, owner, trustees, size = _windows_key_security(os.fspath(path))
    except OSError as exc:
        raise BindingError(f"{what} is not readable") from exc
    try:
        try:
            user = _windows_user_sid() if regular else ""
        except OSError as exc:
            raise BindingError(f"{what} is not readable") from exc
        if not regular or not windows_dacl_allows(owner_sid=owner, user_sid=user, trustees=trustees):
            raise BindingError(
                f"{what} must be a regular file owned by this user and only that user or SYSTEM")
        if size > limit:
            raise BindingError(f"{what} is too large")
        if size == 0:
            return b""
        import ctypes
        from ctypes import wintypes
        raw = ctypes.create_string_buffer(size)
        got = wintypes.DWORD()
        if not kernel32.ReadFile(handle, raw, size, ctypes.byref(got), None):
            raise BindingError(f"{what} is not readable") from OSError(ctypes.get_last_error())
        return raw.raw[:got.value]
    finally:
        kernel32.CloseHandle(handle)


def read_key(path: Path) -> str:
    token = _private_bytes(path, "API key file").decode("utf-8", "replace").strip()
    if not token:
        raise BindingError("API key file is empty")
    return token


def _abs_file(value: Any, name: str) -> Path:
    if not isinstance(value, str) or not os.path.isabs(value):
        raise BindingError(f"{name} must be an absolute path")
    return Path(value)


def _read_json(path: Path, what: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise BindingError(f"{what} is too large")
        data = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise BindingError(f"{what} is unreadable or not JSON") from exc
    if not isinstance(data, dict):
        raise BindingError(f"{what} must be a JSON object")
    return data


def load_binding(path: Path, device: Path | None = None) -> Binding:
    """Load a full binding, or a project file merged onto the device binding `device`."""
    data = _read_json(path, "binding file")
    spool_base = path.parent
    if device is not None:
        base = _read_json(device, "device binding file")
        if set(base) - DEVICE_FIELDS:
            raise BindingError("device binding file has unknown fields")
        extra = sorted(set(data) - PROJECT_FIELDS)
        if extra:
            raise BindingError("project binding may not set " + ", ".join(extra) + " when a device binding is used")
        merged = dict(base)
        for key in ("project_id", "task_id", "step_id"):
            if data.get(key) is not None:
                merged[key] = data[key]
        if data.get("inject_context") is False:  # a project may withhold the pack, never force it on
            merged["inject_context"] = False
        data, spool_base = merged, device.parent
    elif set(data) - ALLOWED:
        raise BindingError("binding file has unknown fields")
    return _build(data, path, spool_base, device)


def load_device(path: Path) -> Binding:
    """The device binding on its own (replay, installer checks): no project ownership."""
    data = _read_json(path, "device binding file")
    if set(data) - DEVICE_FIELDS:
        raise BindingError("device binding file has unknown fields")
    return _build(data, path, path.parent, path)


def _build(data: dict[str, Any], path: Path, spool_base: Path, device: Path | None) -> Binding:
    if data.get("version") != 1:
        raise BindingError("binding version must be 1")
    url = data.get("core_url")
    if not isinstance(url, str) or not (LOOPBACK_URL.match(url) or url == TAILNET_CORE_ORIGIN):
        raise BindingError("core_url must be loopback http://HOST:PORT or exactly " + TAILNET_CORE_ORIGIN
                           + " (an exact match: no trailing slash, port, path or query)")
    for name, prefix in (("host_id", "hst"), ("project_id", "prj"), ("task_id", "tsk"), ("step_id", "stp")):
        value = data.get(name)
        if value is None and name != "host_id":
            continue
        if not isinstance(value, str) or not ids.is_id(value, prefix):
            raise BindingError(f"{name} must be a {prefix}_ id")
    policy = data.get("on_unavailable", "deny")
    if policy not in ("allow", "deny"):
        raise BindingError("on_unavailable must be allow or deny")
    timeout = data.get("timeout_seconds")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 10):
        raise BindingError("timeout_seconds must be in (0, 10]")
    inject = data.get("inject_context", True)
    if type(inject) is not bool:
        raise BindingError("inject_context must be a boolean")
    spool = data.get("spool_dir")
    spool_dir = _abs_file(spool, "spool_dir") if spool else spool_base / "spool"
    human = data.get("human_api_key_file")
    memory_key = data.get("memory_key_file")
    memory_hint = data.get("memory_hint", False)
    if type(memory_hint) is not bool:
        raise BindingError("memory_hint must be a boolean")
    if memory_hint and not memory_key:
        raise BindingError("memory_hint requires memory_key_file")
    return Binding(path=path, core_url=url, host_id=data["host_id"], project_id=data.get("project_id"),
                   task_id=data.get("task_id"), step_id=data.get("step_id"),
                   api_key_file=_abs_file(data.get("api_key_file"), "api_key_file"),
                   human_api_key_file=_abs_file(human, "human_api_key_file") if human else None,
                   on_unavailable=policy, spool_dir=spool_dir,
                   timeout_seconds=float(timeout) if timeout is not None else None, inject_context=inject,
                   device_path=device,
                   memory_key_file=_abs_file(memory_key, "memory_key_file") if memory_key else None,
                   memory_hint=memory_hint)


_DIRECTORY = 0x10
_REPARSE_POINT = 0x400
_OPEN_REPARSE_POINT = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
_ACE_STANDARD = (0, 1, 9, 10)  # allow/deny, including callback ACEs
_ACE_OBJECT = (5, 6, 11, 12)


def _win_api():
    """kernel32 and advapi32. Called only when ``os.name == 'nt'``."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.ReadFile.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    advapi32.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.GetSecurityInfo.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    return ctypes, wintypes, kernel32, advapi32


def _sid_string(ctypes, advapi32, kernel32, sid) -> str:
    if not sid:
        return ""
    text = ctypes.c_wchar_p()
    if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        return ""
    try:
        return text.value or ""
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def _ace_trustees(ctypes, wintypes, advapi32, kernel32, dacl) -> list[str]:
    class AclSize(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD)]

    class AceHeader(ctypes.Structure):
        _fields_ = [("AceType", wintypes.BYTE), ("AceFlags", wintypes.BYTE), ("AceSize", wintypes.WORD)]

    info = AclSize()
    if not advapi32.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
        raise OSError(ctypes.get_last_error())
    trustees = []
    for index in range(info.AceCount):
        ace = ctypes.c_void_p()
        if not advapi32.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
            raise OSError(ctypes.get_last_error())
        header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
        if header.AceType in _ACE_STANDARD:
            sid_at = ace.value + 8
        elif header.AceType in _ACE_OBJECT:
            flags = ctypes.c_uint.from_address(ace.value + 8).value
            sid_at = ace.value + 12 + (16 if flags & 1 else 0) + (16 if flags & 2 else 0)
        else:
            sid_at = 0  # not a known trustee; the decision rejects it
        trustees.append(_sid_string(ctypes, advapi32, kernel32, sid_at))
    return trustees


def _windows_user_sid() -> str:
    ctypes, wintypes, kernel32, advapi32 = _win_api()
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not needed.value:
            raise OSError(ctypes.get_last_error())
        buf = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, 1, buf, needed.value, ctypes.byref(needed)):
            raise OSError(ctypes.get_last_error())
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p)).contents.value
        text = _sid_string(ctypes, advapi32, kernel32, sid)
        if not text:
            raise OSError("current user sid")
        return text
    finally:
        kernel32.CloseHandle(token)


def _windows_key_security(path: str):
    """Open without following a reparse point. Returns handle, regular, owner, trustees, size."""
    ctypes, wintypes, kernel32, advapi32 = _win_api()

    class ByHandle(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    # Do not follow a symlink or junction. The reparse point itself must fail the check.
    handle = kernel32.CreateFileW(path, 0x80000000, 0x7, None, 3, _OPEN_REPARSE_POINT, None)
    invalid = ctypes.c_void_p(-1).value
    if not handle or handle == invalid:
        raise OSError(ctypes.get_last_error())
    try:
        info = ByHandle()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error())
        if info.dwFileAttributes & (_DIRECTORY | _REPARSE_POINT):
            return kernel32, handle, False, "", None, 0
        owner = ctypes.c_void_p()
        group = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        sacl = ctypes.c_void_p()
        sd = ctypes.c_void_p()
        status = advapi32.GetSecurityInfo(
            handle, 1, 0x1 | 0x4, ctypes.byref(owner), ctypes.byref(group),
            ctypes.byref(dacl), ctypes.byref(sacl), ctypes.byref(sd))
        if status != 0:
            raise OSError(status)
        try:
            owner_sid = _sid_string(ctypes, advapi32, kernel32, owner.value)
            trustees = None if not dacl.value else _ace_trustees(ctypes, wintypes, advapi32, kernel32, dacl)
        finally:
            if sd.value:
                kernel32.LocalFree(sd)
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        return kernel32, handle, True, owner_sid, trustees, size
    except Exception:
        kernel32.CloseHandle(handle)
        raise
