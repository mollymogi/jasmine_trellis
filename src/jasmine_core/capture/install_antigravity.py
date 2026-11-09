"""Own one named hook in Antigravity's user-level `hooks.json` (ADR 0042).

`~/.gemini/config/hooks.json` maps hook names to event handlers; Antigravity
merges every named hook. The installer owns only the `jasmine-trellis` entry:
it keeps every other key, refuses a symlinked or malformed file, writes a
backup first, and on `--disable` removes only its own entry. The Memory hint is
the device binding's opt-in, read by the capture module.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import sys

from ..hosthook.binding import load_device, read_key
from ..hosthook.install import InstallError, _backup, _write
from . import memory_hint
from .antigravity_session import EVENTS

NAME = "jasmine-trellis"
#: Seconds per handler. PreInvocation runs before every model call but returns at
#: once unless it is the turn's first; SessionStart/PreInvocation may ask Core.
TIMEOUTS = {"SessionStart": 10, "PreInvocation": 10, "Stop": 10}


def _document(target: Path) -> tuple[bytes | None, dict]:
    if target.is_symlink():
        raise InstallError("symlinked hooks.json refused")
    original = target.read_bytes() if target.exists() else None
    document = json.loads(original) if original else {}
    if not isinstance(document, dict):
        raise InstallError("hooks.json is not a JSON object")
    return original, document


def install(home: Path, binding: Path, receipts: Path, *, python: str, launcher: Path,
            gemini_home: Path | None = None, confirmed: bool = False, disable: bool = False) -> dict:
    """`home` is Antigravity's user customization root (`~/.gemini/config`)."""
    if not confirmed:
        raise InstallError("user-wide Antigravity capture requires explicit opt-in")
    if home.is_symlink():
        raise InstallError("symlinked Antigravity configuration refused")
    device = load_device(binding)
    read_key(device.api_key_file)
    if device.human_api_key_file is None:
        raise InstallError("separate prompt credential required")
    read_key(device.human_api_key_file)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = home / "hooks.json"
    with (home / ".jasmine-trellis-hooks.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        original, document = _document(target)
        if disable:
            memory_hint.set_enabled(binding, False)  # the hint never outlives capture
            if NAME not in document:
                return {"status": "disabled", "config": str(target)}
            document.pop(NAME)
            backup = _backup(target, original)
            _write(target, document)
            return {"status": "disabled", "config": str(target), "backup": str(backup) if backup else None}
        base = [str(Path(python).absolute()), "-I", "-B", str(launcher.resolve(strict=True)),
                "--device-binding", str(binding.resolve(strict=True)), "--receipts", str(receipts.absolute()),
                "--gemini-home", str((gemini_home or Path.home() / ".gemini").absolute())]
        # All three are flat lists of handlers (no matcher); PreToolUse is never
        # registered: a hook there that states no decision blocks the tool.
        document[NAME] = {event: [{"type": "command", "command": shlex.join([*base, "--event", event]),
                                   "timeout": TIMEOUTS[event]}] for event in EVENTS}
        encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
        if original == encoded:
            return {"status": "unchanged", "config": str(target)}
        backup = _backup(target, original)
        _write(target, document)
        target.chmod(0o600)
        return {"status": "installed", "config": str(target), "backup": str(backup) if backup else None,
                "events": list(EVENTS), "trust": "Antigravity reads hooks.json on its next session"}


def set_memory_hint(home: Path, binding: Path, *, enabled: bool, confirmed: bool = False) -> dict:
    """ADR 0036/0039: hooks.json is unchanged; the opt-in lives in the device binding."""
    if not confirmed:
        raise InstallError("Memory hint requires explicit opt-in")
    if enabled:
        _, document = _document(home / "hooks.json")
        if NAME not in document:
            raise InstallError("enable conversation capture before the Memory hint")
    if not memory_hint.set_enabled(binding, enabled):
        return {"status": "unchanged"}
    return {"status": "memory_hint_enabled" if enabled else "memory_hint_disabled"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("device-binding", "receipts", "launcher"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--antigravity-home", type=Path, default=Path.home() / ".gemini" / "config",
                   help="Antigravity's user customization root (holds hooks.json)")
    p.add_argument("--gemini-home", type=Path, default=None, help="~/.gemini (holds the conversation transcripts)")
    p.add_argument("--confirm-user-scope", action="store_true")
    p.add_argument("--disable", action="store_true")
    p.add_argument("--memory-hint", choices=["enable", "disable"])
    a = p.parse_args()
    os.umask(0o077)
    if a.memory_hint:
        print(json.dumps(set_memory_hint(a.antigravity_home, a.device_binding, enabled=a.memory_hint == "enable",
                                         confirmed=a.confirm_user_scope)))
        return
    print(json.dumps(install(a.antigravity_home, a.device_binding, a.receipts, python=sys.executable,
                             launcher=a.launcher, gemini_home=a.gemini_home, confirmed=a.confirm_user_scope,
                             disable=a.disable)))


if __name__ == "__main__":
    main()
