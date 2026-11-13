"""Opt-in native DeepSeek Harness capture through a user-level dsh plugin.

dsh has no command hooks. The Jasmine plugin (written by `install_dsh`) runs
inside the dsh process and forwards four moments as one JSON object on stdin:

* `session.created` → `session.started`
* `user.prompt`     → `user.prompt` (a human `user` / `user-rpc` message only)
* `assistant.message` → `assistant.message`
* `session.compacting` → a PRE_COMPACT session Checkpoint, no Event

The plugin sends the text it already holds. This module does not read
`session.v4.jsonl.zstd`. It only checks that dsh's own session directory
exists for that id and working directory, and that the session is not a
subagent. On `user.prompt` stdout is `{"context": text|null}`: the resume
block and the Memory hint, which the plugin attaches as its own message.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

from ..canonical import canonical_json
from ..hosthook.binding import load_device
from . import memory_hint, worklog_client
from .hook_budget import arm_tailnet, disarm
from .codex_session import RECENT_SECONDS, append_receipt, workspace_root
from .grok_session import _publish, bind_workspace

SOURCE = "dsh-native-conversation-v1"
AGENT = "dsh"
EVENTS = {"session.created": "SessionStart", "user.prompt": "UserPromptSubmit",
          "assistant.message": "Stop", "session.compacting": "PreCompact"}
MAX_TEXT = 512 * 1024
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Persistence mints `session-<uuid>`. The in-memory store mints `session-<n>`.
_SESSION = re.compile(r"^session-[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


def project_key(cwd: str) -> str:
    """dsh's human-navigable project directory name (`projectKey` in its persistence)."""
    if not cwd:
        raise ValueError("cannot encode an empty project path")
    readable = []
    separator = False
    for ch in cwd:
        if ch in "/\\:":
            if not separator:
                readable.append("-")
            separator = True
        elif ch != "~" and re.fullmatch(r"[A-Za-z0-9._-]", ch):
            readable.append(ch)
            separator = False
        else:
            readable.append("~" + format(ord(ch), "04X"))
            separator = False
    body = "".join(readable).lstrip("-") or "root"
    return f"--{body[:251]}--"


def encode_segment(raw: str) -> str:
    """One safe path segment, matching dsh `encodeSegment` for BMP text."""
    if raw == "":
        raise ValueError("cannot encode an empty path segment")
    if raw == ".":
        return "~002E"
    if raw == "..":
        return "~002E~002E"
    out = []
    for ch in raw:
        if ch != "~" and re.fullmatch(r"[A-Za-z0-9._-]", ch):
            out.append(ch)
        else:
            out.append("~" + format(ord(ch), "04X"))
    return "".join(out)


def normalize(native: Any) -> dict[str, Any] | None:
    """The plugin's message, checked. None for something capture ignores."""
    if not isinstance(native, dict):
        raise ValueError("native event must be an object")
    event = EVENTS.get(native.get("event"))
    if event is None:
        return None
    if native.get("origin") == "subagent" or native.get("delegationDepth", 0) not in (0, None):
        return None
    sid = native.get("sessionID")
    cwd = native.get("cwd")
    if not isinstance(sid, str) or not (_UUID.match(sid) or _SESSION.match(sid)) or not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("invalid native identity")
    stamp = native.get("timestamp")
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("native timestamp required")
    turn = native.get("messageID")
    if event in ("UserPromptSubmit", "Stop") and (not isinstance(turn, str) or not _UUID.match(turn)):
        raise ValueError("native message id required")
    text = native.get("text", "") if event in ("UserPromptSubmit", "Stop") else ""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("native message is not bounded text")
    if event in ("UserPromptSubmit", "Stop") and not text.strip():
        return None
    starting = native.get("starting") is True
    return {"event": event, "sid": sid, "turn": turn if event in ("UserPromptSubmit", "Stop") else None,
            "text": text, "timestamp": stamp, "cwd": str(Path(cwd).resolve(strict=True)),
            "source": "new" if starting else None, "starting": starting}


def session_origin(n: dict[str, Any], dsh_home: Path) -> dict[str, Any] | None:
    """dsh's session directory for this id and working directory. No log body is read."""
    try:
        home = dsh_home.resolve(strict=True)
        sessions = home / "sessions"
        folder = sessions / project_key(n["cwd"]) / encode_segment(n["sid"])
        if any(path.is_symlink() for path in (home, sessions, folder.parent, folder)):
            return None
        info = folder.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return None
    except (OSError, ValueError):
        return None
    identity = canonical_json([n["sid"], n["cwd"]])
    return {"check": "dsh_session_directory", "identity_sha256": hashlib.sha256(identity.encode()).hexdigest()}


def capture_device(native: Any, device, dsh_home: Path) -> dict[str, Any]:
    n = normalize(native)
    if n is None:
        return {"status": "ignored", "reason": "unsupported_or_empty"}
    origin = session_origin(n, dsh_home)
    if origin is None:
        return {"status": "ignored", "reason": "unverified_session_origin", "native_session_id": n["sid"]}
    binding = bind_workspace(n, device, source=SOURCE)
    if n["event"] == "PreCompact":
        return {"status": "accepted", "agent": AGENT, "native_hook_event": "PreCompact", "native_session_id": n["sid"],
                "project_id": binding.project_id,
                "worklog": worklog_client.record(binding, project_id=binding.project_id, native_session_id=n["sid"],
                                                 reason="PRE_COMPACT", workspace_root=workspace_root(n["cwd"]))}
    receipt = _publish(n, binding, origin, source=SOURCE, agent=AGENT)
    receipt["workspace"] = n["cwd"]
    if n["starting"]:
        receipt["starting"] = True
    return receipt


def work_record(native: dict[str, Any], device, receipt: dict[str, Any]) -> str | None:
    """ADR 0039 for one accepted event. Returns the text for the user's message."""
    event, sid = receipt.get("event_type"), receipt["native_session_id"]
    root = workspace_root(receipt["workspace"])
    if event == "session.started":
        receipt["worklog"] = worklog_client.recover(device, workspace_root=root, native_session_id=sid)
        return None
    if event != "user.prompt" or not device.memory_hint:
        return None
    prompt = native.get("text")
    starting = receipt.get("starting") is True and worklog_client.recent_query(prompt) is None
    resume, answer = worklog_client.resume_block(device, native_session_id=sid, workspace_root=root,
                                                 starting=starting, prompt=prompt,
                                                 start_seconds=memory_hint.BUDGET_SECONDS,
                                                 prompt_seconds=RECENT_SECONDS)
    if answer is not None:
        receipt["recent_activity"] = answer
    hint, receipt["memory_hint"] = memory_hint.request(device, project_id=receipt["project_id"], native_session_id=sid,
                                                       session_id=receipt["session_id"], prompt=prompt)
    return "\n\n".join(t for t in (resume, hint) if t) or None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--device-binding", required=True, type=Path)
    p.add_argument("--receipts", required=True, type=Path)
    p.add_argument("--dsh-home", required=True, type=Path)
    args = p.parse_args()
    os.umask(0o077)
    native: Any = {}
    context = None
    budget = None
    try:
        native = json.load(sys.stdin)
        device = load_device(args.device_binding)
        budget = arm_tailnet(device.core_url, native.get("event") if isinstance(native, dict) else None)
        receipt = capture_device(native, device, args.dsh_home)
        if receipt.get("status") == "accepted" and receipt.get("event_type"):
            try:
                context = work_record(native, device, receipt)
            except Exception as exc:
                receipt["work_record"] = {"status": "failed", "error_type": type(exc).__name__}
        receipt.pop("workspace", None)
    except Exception as exc:
        receipt = {"status": "failed", "agent": AGENT, "error_type": type(exc).__name__,
                   "native_event": native.get("event") if isinstance(native, dict) else None}
        print("Jasmine dsh capture failed; inspect private receipts.", file=sys.stderr)
    finally:
        disarm(budget)
    receipt["checked_at"] = datetime.now(timezone.utc).isoformat()
    if context:
        receipt["context_chars"] = len(context)
    append_receipt(args.receipts, receipt)
    print(json.dumps({"context": context}, ensure_ascii=False))
    return 1 if receipt["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
