#!/usr/bin/env python3
"""Enroll one tailnet client on the hub. Writes core.db directly.

Not an HTTP route. The operator runs this on the hub and passes ``--db``,
``--display-name``, ``--confirm``, one or more ``--agent``, and ``--bundle``.
The script does not scan the tailnet, start ``tailscale serve``, or copy hub
keys out of ``config/credentials``.

Steps:

1. Refuse Windows (``--platform windows``, or a ``--display-name`` that
   indicates Windows) before any database or bundle write.
2. Create a new ``hst_`` (``kind=workstation``) and a ``tailnet_clients`` row
   with ``status=active``.
3. Create one system actor per named agent, ``home_host_id`` that ``hst_``.
4. Create one new human actor for that host (``kind=human``,
   ``home_host_id`` that ``hst_``). Do not reuse the hub browser human.
5. Issue three keys per agent, each with ``bound_host_id`` set to that host:
   system (``objects:write`` + ``events:write``), human (``events:write``
   only, the new human actor), memory (``memory:recall`` only, the same
   system actor).
6. Write the device binding and the three key files under ``--bundle`` only.
   The bundle directory is mode ``0700``. Key files are mode ``0600``.

The device binding ``core_url`` is exactly ``CORE_URL``
(``https://mercuryliumac-mini.tail07df76.ts.net``) with no trailing slash.
Install check, on the client, after the bundle is copied and the key and
spool paths are rewritten to absolute paths on that machine: run
``load_device`` on the device binding. The loaded ``core_url`` must equal
``CORE_URL``. The client does not read ``JASMINE_TAILNET_CLIENTS``.

``memory_hint`` stays false. The operator turns the hint on later on that
client. A Windows display name contains ``windows`` (any case) or starts
with ``WIN-`` (Windows' default computer name, for example
``WIN-DRUN38VBEEM``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jasmine_core import auth, clock, db, ids, registry  # noqa: E402

#: Exact origin the client binding must load. No trailing slash, no env override.
CORE_URL = "https://mercuryliumac-mini.tail07df76.ts.net"
AGENTS = ("codex", "claude", "grok", "opencode", "antigravity", "dsh")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_WINDOWS_PLATFORM = frozenset({"windows", "win32", "nt"})
_ALLOWED_PLATFORM = frozenset({"linux", "darwin", "macos"})


class EnrollmentError(Exception):
    """Refusal. The message is safe to print and contains no token."""


def indicates_windows(display_name: str, platform: str | None) -> bool:
    if platform is not None and platform.casefold() in _WINDOWS_PLATFORM:
        return True
    folded = display_name.casefold()
    return "windows" in folded or folded.startswith("win-")


def _under_home(path: Path) -> bool:
    home = Path.home().resolve()
    resolved = path.resolve()
    return resolved == home or home in resolved.parents


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def enroll(database: Path, display_name: str, agents: list[str], bundle: Path, *,
           platform: str | None, confirmed: bool) -> dict[str, object]:
    if not confirmed:
        raise EnrollmentError("--confirm is required")
    if not isinstance(display_name, str) or not _NAME.match(display_name):
        raise EnrollmentError("--display-name must be a hostname")
    if indicates_windows(display_name, platform):
        raise EnrollmentError("Windows enrollment is refused")
    if platform is not None and platform.casefold() not in _ALLOWED_PLATFORM:
        raise EnrollmentError(f"unknown --platform {platform}")
    if not agents or len(set(agents)) != len(agents) or any(agent not in AGENTS for agent in agents):
        raise EnrollmentError("--agent must name one or more of " + ", ".join(AGENTS))
    bundle = Path(bundle)
    if bundle.exists() and bundle.is_symlink():
        raise EnrollmentError("--bundle must not be a symlink")
    if _under_home(bundle):
        raise EnrollmentError("refusing to write under the home directory")
    if not database.is_file():
        raise EnrollmentError("database does not exist")
    if bundle.exists() and any(bundle.iterdir()):
        raise EnrollmentError("bundle directory is not empty")

    conn = db.connect(database)
    try:
        reg = registry.Registry(conn)
        host_id = ids.new_id("hst")
        human_id = ids.new_id("act")
        issued: list[tuple[str, str, dict[str, object]]] = []
        with db.transaction(conn):
            reg.upsert_host(host_id, display_name=display_name, kind="workstation")
            conn.execute(
                "INSERT INTO tailnet_clients (host_id, display_name, status, created_at, revoked_at)"
                " VALUES (?, ?, 'active', ?, NULL)",
                (host_id, display_name, clock.now_rfc3339()),
            )
            reg.upsert_actor(human_id, kind="human", display_name=f"{display_name} human",
                             home_host_id=host_id)
        actors: dict[str, str] = {}
        for agent in agents:
            actor_id = ids.new_id("act")
            with db.transaction(conn):
                reg.upsert_actor(
                    actor_id, kind="system",
                    display_name=f"{agent.capitalize()} native conversation capture",
                    home_host_id=host_id,
                )
            actors[agent] = actor_id
            gate = auth.Auth(conn)
            issued.append((agent, "system", gate.issue_key(
                actor_id=actor_id, label=f"{agent} native system {display_name}",
                scopes=["objects:write", "events:write"], bound_host_id=host_id,
            )))
            issued.append((agent, "human", gate.issue_key(
                actor_id=human_id, label=f"{agent} native user prompts {display_name}",
                scopes=["events:write"], bound_host_id=host_id,
            )))
            issued.append((agent, "memory", gate.issue_key(
                actor_id=actor_id, label=f"{agent} native Memory hint {display_name}",
                scopes=["memory:recall"], bound_host_id=host_id,
            )))
    finally:
        conn.close()

    bundle.mkdir(mode=0o700, exist_ok=True)
    bundle.chmod(0o700)
    for agent, kind, key in issued:
        _write_private(bundle / f"{agent}-conversation-{kind}.key", (str(key["token"]) + "\n").encode())
    bindings: dict[str, str] = {}
    for agent in agents:
        binding = bundle / f"{agent}-conversation-device.json"
        document = {
            "version": 1,
            "core_url": CORE_URL,
            "host_id": host_id,
            "api_key_file": str(bundle / f"{agent}-conversation-system.key"),
            "human_api_key_file": str(bundle / f"{agent}-conversation-human.key"),
            "memory_key_file": str(bundle / f"{agent}-conversation-memory.key"),
            "memory_hint": False,
            "inject_context": False,
            "on_unavailable": "allow",
            "spool_dir": str(bundle / f"{agent}-capture-spool"),
        }
        _write_private(binding, (json.dumps(document, indent=2) + "\n").encode())
        bindings[agent] = str(binding)
    return {
        "host_id": host_id,
        "display_name": display_name,
        "human_actor_id": human_id,
        "agents": {agent: {"system_actor_id": actors[agent], "binding": bindings[agent]} for agent in agents},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--agent", action="append", required=True, choices=AGENTS)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--confirm", action="store_true", required=True)
    args = parser.parse_args(argv)
    # Windows is refused before enroll() opens the database or creates --bundle.
    if indicates_windows(args.display_name, args.platform):
        print("Windows enrollment is refused", file=sys.stderr)
        return 1
    try:
        result = enroll(args.db, args.display_name, list(args.agent), args.bundle,
                        platform=args.platform, confirmed=args.confirm)
    except EnrollmentError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
