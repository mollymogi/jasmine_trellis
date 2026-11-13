"""Opt-in native OpenCode capture through a user-level plugin (ADR 0040).

OpenCode has no command hooks; its plugins run inside the OpenCode server. The
Jasmine plugin (`opencode_plugin.js`, written by `install_opencode`) forwards
four moments to this module as one JSON object on stdin:

* `session.created` → `session.started`
* `chat.message`    → `user.prompt` (the user's own text parts, no synthetic ones)
* `session.idle`    → `assistant.message` (the last finished assistant reply)
* `session.compacting` → a PRE_COMPACT session Checkpoint (ADR 0039), no Event

The plugin sends OpenCode's ids, not paths. Every session is looked up in
OpenCode's own database (read-only): it must exist and have no parent, so a
subagent session or a made-up id records nothing. Its directory comes from that
row, and a reply's text from that message's saved text parts. On `chat.message` the reply
on stdout is `{"context": text|null}`: the resume block and the Memory hint,
which the plugin attaches to the user's message as a synthetic part.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Any

from ..canonical import canonical_json
from ..hosthook.binding import load_device
from . import memory_hint, worklog_client
from .codex_session import RECENT_SECONDS, append_receipt, workspace_root
from .grok_session import _publish, bind_workspace
from .hook_budget import arm_tailnet, disarm

SOURCE = "opencode-native-conversation-v1"
AGENT = "opencode"
#: Plugin events, as the Grok publisher names them (it keys Event types by these).
EVENTS = {"session.created": "SessionStart", "chat.message": "UserPromptSubmit",
          "session.idle": "Stop", "session.compacting": "PreCompact"}
MAX_TEXT = 512 * 1024
_SID = re.compile(r"^ses_[A-Za-z0-9]{8,64}$")
_MID = re.compile(r"^msg_[A-Za-z0-9]{8,64}$")


def default_data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "opencode"


def normalize(native: Any) -> dict[str, Any] | None:
    """The plugin's message, checked. None for something capture ignores."""
    if not isinstance(native, dict):
        raise ValueError("native event must be an object")
    event = EVENTS.get(native.get("event"))
    if event is None:
        return None
    sid = native.get("sessionID")
    if not isinstance(sid, str) or not _SID.match(sid):
        raise ValueError("invalid native identity")
    stamp = native.get("timestamp")
    if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("native timestamp required")
    turn = native.get("messageID")
    if event in ("UserPromptSubmit", "Stop") and (not isinstance(turn, str) or not _MID.match(turn)):
        raise ValueError("native message id required")
    text = native.get("text", "") if event == "UserPromptSubmit" else ""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("native message is not bounded text")
    if event == "UserPromptSubmit" and not text.strip():
        return None
    starting = native.get("starting") is True
    return {"event": event, "sid": sid, "turn": turn if event in ("UserPromptSubmit", "Stop") else None,
            "text": text, "timestamp": stamp, "source": "new" if starting else None, "starting": starting}


def _reply_text(conn: sqlite3.Connection, message_id: str) -> str:
    """The assistant reply's own text parts, in order (no tool output, no synthetic parts)."""
    texts = []
    for (data,) in conn.execute("SELECT data FROM part WHERE message_id=? ORDER BY id", (message_id,)):
        part = json.loads(data)
        if part.get("type") == "text" and not part.get("synthetic") and not part.get("ignored") \
                and isinstance(part.get("text"), str):
            texts.append(part["text"])
    return "\n\n".join(t for t in texts if t.strip())


def session_origin(n: dict[str, Any], data_home: Path) -> dict[str, Any] | None:
    """OpenCode's own record of this session: present and a main session.

    Fills `n["cwd"]` from the session's directory and, for a reply, `n["text"]`
    from its saved parts. Reads metadata and that one reply only.
    """
    database = data_home / "opencode.db"
    try:
        if data_home.is_symlink() or database.is_symlink():
            return None
        info = database.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
        conn = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=1)
        try:
            row = conn.execute("SELECT directory, parent_id FROM session WHERE id=?", (n["sid"],)).fetchone()
            if row is None or row[1] is not None or not isinstance(row[0], str) or not Path(row[0]).is_absolute():
                return None
            if n["event"] == "UserPromptSubmit":
                # chat.message runs before OpenCode saves the message; its id must not be another session's.
                if conn.execute("SELECT 1 FROM message WHERE id=? AND session_id<>?", (n["turn"], n["sid"])).fetchone():
                    return None
            elif n["event"] == "Stop":
                # The reply is already saved: it must be this session's finished assistant message.
                message = conn.execute("SELECT data FROM message WHERE id=? AND session_id=?",
                                       (n["turn"], n["sid"])).fetchone()
                body = json.loads(message[0]) if message else {}
                if body.get("role") != "assistant" or not (body.get("time") or {}).get("completed") or body.get("summary"):
                    return None
                n["text"] = _reply_text(conn, n["turn"])
                if not n["text"] or len(n["text"]) > MAX_TEXT:
                    return None
        finally:
            conn.close()
        n["cwd"] = str(Path(row[0]).resolve(strict=True))
    except (OSError, ValueError, sqlite3.Error):
        return None
    identity = canonical_json([n["sid"], n["cwd"]])
    return {"check": "opencode_session_row", "identity_sha256": hashlib.sha256(identity.encode()).hexdigest()}


def capture_device(native: Any, device, data_home: Path) -> dict[str, Any]:
    event = native.get("event") if isinstance(native, dict) else None
    token = arm_tailnet(device.core_url, event)
    try:
        return _capture_device(native, device, data_home)
    finally:
        disarm(token)


def _capture_device(native: Any, device, data_home: Path) -> dict[str, Any]:
    n = normalize(native)
    if n is None:
        return {"status": "ignored", "reason": "unsupported_or_empty"}
    origin = session_origin(n, data_home)
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
    # The plugin marks the first prompt of a session it saw created as starting.
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
    p.add_argument("--opencode-data", type=Path, default=None, help="OpenCode's data folder (holds opencode.db)")
    args = p.parse_args()
    os.umask(0o077)
    native: Any = {}
    context = None
    budget = None
    try:
        native = json.load(sys.stdin)
        device = load_device(args.device_binding)
        budget = arm_tailnet(device.core_url, native.get("event") if isinstance(native, dict) else None)
        receipt = capture_device(native, device, args.opencode_data or default_data_home())
        if receipt.get("status") == "accepted" and receipt.get("event_type"):
            try:
                context = work_record(native, device, receipt)
            except Exception as exc:
                receipt["work_record"] = {"status": "failed", "error_type": type(exc).__name__}
        receipt.pop("workspace", None)  # receipts hold ids, not paths
    except Exception as exc:  # no bearer, message text or raw HTTP body in stderr
        receipt = {"status": "failed", "agent": AGENT, "error_type": type(exc).__name__,
                   "native_event": native.get("event") if isinstance(native, dict) else None}
        print("Jasmine OpenCode capture failed; inspect private receipts.", file=sys.stderr)
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
