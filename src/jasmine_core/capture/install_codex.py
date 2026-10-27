"""Install only conversation capture in the user's Codex configuration.

This does not enable Gate, inject Context, approve Rules, or write Memory.
Codex itself owns hook trust; the installer never reads or modifies trust hashes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys

from ..hosthook.binding import load_device, read_key
from ..hosthook.install import _backup, _write, InstallError
from .codex_session import EVENTS
from . import memory_hint

LABEL = "Jasmine daily conversation capture"


def install(home: Path, binding: Path, receipts: Path, *, python: str = sys.executable,
            confirmed: bool = False, disable: bool = False) -> dict:
    if not confirmed:
        raise InstallError("user-wide conversation capture requires explicit opt-in")
    device = load_device(binding)
    read_key(device.api_key_file)
    if device.human_api_key_file is None:
        raise InstallError("a separate human prompt key is required")
    read_key(device.human_api_key_file)
    target = home / "hooks.json"
    if target.is_symlink() or home.is_symlink():
        raise InstallError("refusing symlinked Codex configuration")
    original = target.read_bytes() if target.exists() else None
    document = json.loads(original) if original else {"hooks": {}}
    if not isinstance(document, dict) or not isinstance(document.setdefault("hooks", {}), dict):
        raise InstallError("invalid hooks document")
    command = shlex.join([python, "-I", "-B", "-m", "jasmine_core.capture.codex_session",
                          "--device-binding", str(binding.resolve(strict=True)),
                          "--receipts", str(receipts.absolute())])
    for event in EVENTS:
        groups = document["hooks"].get(event, [])
        if not isinstance(groups, list):
            raise InstallError("invalid hook groups")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise InstallError("invalid hook handlers")
            remaining = []
            for handler in group["hooks"]:
                owned = False
                if isinstance(handler, dict) and handler.get("statusMessage") == LABEL:
                    try:
                        words = shlex.split(handler.get("command", ""))
                        owned = ("jasmine_core.capture.codex_session" in words and "--device-binding" in words)
                    except ValueError:
                        pass
                if not owned:
                    remaining.append(handler)
            if remaining:
                kept.append({**group, "hooks": remaining})
        if not disable:
            kept.append({"hooks": [{"type": "command", "command": command,
                               "timeout": 3 if event == "SessionEnd" else 10,
                               "statusMessage": LABEL}]})
        document["hooks"][event] = kept
    if disable:
        memory_hint.set_enabled(binding, False)  # the hint never outlives capture
    if original == (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode():
        return {"status": "disabled" if disable else "unchanged", "config": str(target)}
    backup = _backup(target, original)
    _write(target, document)
    return {"status": "disabled" if disable else "installed", "config": str(target), "backup": str(backup) if backup else None,
            "events": list(EVENTS), "trust": "review the four hooks in Codex /hooks"}


def set_memory_hint(home: Path, binding: Path, *, enabled: bool, confirmed: bool = False) -> dict:
    """ADR 0036. Codex's hook command bytes stay unchanged (so Codex's own hook
    trust is kept); the opt-in lives in the device binding the hook reads."""
    if not confirmed:
        raise InstallError("Memory hint requires explicit opt-in")
    if enabled:
        target = home / "hooks.json"
        document = json.loads(target.read_bytes()) if target.exists() and not target.is_symlink() else {}
        handlers = [h for g in document.get("hooks", {}).get("UserPromptSubmit", []) if isinstance(g, dict)
                    for h in g.get("hooks", []) if isinstance(h, dict) and h.get("statusMessage") == LABEL]
        if not handlers:
            raise InstallError("enable conversation capture before the Memory hint")
    changed = memory_hint.set_enabled(binding, enabled)
    if not changed:
        return {"status": "unchanged"}
    return {"status": "memory_hint_enabled" if enabled else "memory_hint_disabled"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-binding", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--confirm-user-scope", action="store_true")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--memory-hint", choices=["enable", "disable"])
    args = parser.parse_args()
    home = args.codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if args.memory_hint:
        print(json.dumps(set_memory_hint(home, args.device_binding, enabled=args.memory_hint == "enable",
                                         confirmed=args.confirm_user_scope), ensure_ascii=False))
        return
    print(json.dumps(install(home, args.device_binding, args.receipts,
                             confirmed=args.confirm_user_scope, disable=args.disable), ensure_ascii=False))


if __name__ == "__main__":
    main()
