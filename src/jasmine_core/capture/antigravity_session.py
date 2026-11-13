"""Opt-in native Antigravity capture through its lifecycle hooks (ADR 0042).

Antigravity (CLI 1.5+, `agy`) runs command hooks named in a `hooks.json`; the
user-level one is `~/.gemini/config/hooks.json`. Its payload does not name the
event, so each registered command passes `--event`:

* `SessionStart`  → `session.started`; the resume block is injected
* `PreInvocation` with `invocationNum == 0` (the turn's first model call) →
  `user.prompt`; the resume block (when asked) and the Memory hint are injected
* `Stop` with `terminationReason == NO_TOOL_CALL` → `assistant.message`

Injection is `{"injectSteps": [{"systemMessage": {"systemMessage": text}}]}`,
which Antigravity adds to the conversation before the model runs.

Every event is checked against Antigravity's own transcript
(`~/.gemini/<product>/brain/<conversation>/.system_generated/logs/transcript_full.jsonl`):
the prompt must be the transcript's `USER_INPUT` step from `USER_EXPLICIT`
with exactly the hook's text, and the reply its last finished
`PLANNER_RESPONSE` without tool calls. A subagent's or a scheduled turn's
input has another source and records nothing. There is no session-end or
compaction hook: unclosed sessions are recovered by the next start (ADR 0039).
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
import uuid

from ..canonical import canonical_json
from ..hosthook.binding import load_device
from . import memory_hint, worklog_client
from .codex_session import RECENT_SECONDS, append_receipt, workspace_root
from .grok_session import _publish, bind_workspace
from .hook_budget import arm_tailnet, disarm

SOURCE = "antigravity-native-conversation-v1"
AGENT = "antigravity"
#: Hook events, as the shared publisher names them.
EVENTS = ("SessionStart", "PreInvocation", "Stop")
PUBLISHED = {"SessionStart": "SessionStart", "PreInvocation": "UserPromptSubmit", "Stop": "Stop"}
#: Antigravity's products keep their conversations under these folders of ~/.gemini.
PRODUCTS = ("antigravity-cli", "antigravity", "antigravity-ide")
TRANSCRIPT_TAIL = 4 * 1024 * 1024
MAX_TEXT = 512 * 1024
_REQUEST = re.compile(r"<USER_REQUEST>\n?(.*?)\n?</USER_REQUEST>", re.S)


def _uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        return False


def normalize(event: str, native: Any) -> dict[str, Any] | None:
    """The hook payload, checked. None for something capture ignores."""
    if event not in EVENTS:
        raise ValueError("unsupported hook event")
    if not isinstance(native, dict):
        raise ValueError("native event must be an object")
    sid = native.get("conversationId")
    paths = native.get("workspacePaths")
    if not _uuid(sid) or not isinstance(paths, list) or not paths or not isinstance(paths[0], str) \
            or not Path(paths[0]).is_absolute():
        raise ValueError("invalid native identity")
    if event == "PreInvocation" and native.get("invocationNum") != 0:
        return None  # later model calls of the same turn
    if event == "Stop" and native.get("terminationReason") != "NO_TOOL_CALL":
        return None  # cancelled, failed or cut short: no finished reply
    text = native.get("lastUserInput") if event == "PreInvocation" else ""
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError("native message is not bounded text")
    if event == "PreInvocation" and not text.strip():
        return None
    first_step = native.get("initialNumSteps")
    if event == "PreInvocation" and (type(first_step) is not int or first_step < 1):
        raise ValueError("initialNumSteps required")
    return {"hook": event, "event": PUBLISHED[event], "sid": sid, "text": text, "turn": None,
            "native_cwd": paths[0], "cwd": str(Path(paths[0]).resolve(strict=True)),
            "transcript": native.get("transcriptPath"), "first_step": first_step}


def _transcript_path(n: dict[str, Any], gemini_home: Path) -> Path | None:
    supplied = n.get("transcript")
    if not isinstance(supplied, str) or not Path(supplied).is_absolute():
        return None
    home = gemini_home.resolve()
    for product in PRODUCTS:
        expected = home / product / "brain" / n["sid"] / ".system_generated" / "logs" / "transcript_full.jsonl"
        if Path(supplied) == expected or Path(supplied).resolve() == expected:
            return expected
    return None


def _steps(path: Path) -> list[dict[str, Any]] | None:
    """The transcript's steps (a bounded tail), or None when the file is not the user's own."""
    try:
        if path.is_symlink() or path.parent.is_symlink():
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
        start = max(0, info.st_size - TRANSCRIPT_TAIL)
        os.lseek(fd, start, os.SEEK_SET)
        raw = b""
        while len(raw) < info.st_size - start:
            chunk = os.read(fd, info.st_size - start - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if start:
        lines = lines[1:]  # the first line of a tail may be cut
    steps = []
    for line in lines:
        try:
            step = json.loads(line)
        except ValueError:
            continue
        if isinstance(step, dict) and type(step.get("step_index")) is int:
            steps.append(step)
    return steps


def session_origin(n: dict[str, Any], gemini_home: Path) -> dict[str, Any] | None:
    """Antigravity's own record of this turn. Fills `turn`, `timestamp` and, for a
    reply, `text` from the transcript."""
    path = _transcript_path(n, gemini_home)
    if path is None:
        return None
    try:
        folder = path.parents[2]  # brain/<conversation>
        info = folder.stat()
        if folder.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return None
    except OSError:
        return None
    if n["hook"] == "SessionStart":
        # The transcript may not exist yet; the conversation folder is Antigravity's.
        n["timestamp"] = datetime.now(timezone.utc).isoformat()
        n["timestamp_source"] = "hook_observation"
    else:
        steps = _steps(path)
        if not steps:
            return None
        if n["hook"] == "PreInvocation":
            index = n["first_step"] - 1
            step = next((s for s in steps if s["step_index"] == index), None)
            if step is None or step.get("type") != "USER_INPUT" or step.get("source") != "USER_EXPLICIT":
                return None
            words = _REQUEST.search(step.get("content") or "")
            if words is None or words.group(1).strip() != n["text"].strip():
                return None
        else:
            users = [s["step_index"] for s in steps if s.get("type") == "USER_INPUT"]
            if not users:
                return None
            replies = [s for s in steps if s["step_index"] > users[-1] and s.get("type") == "PLANNER_RESPONSE"
                       and s.get("status") == "DONE" and not s.get("tool_calls")
                       and isinstance(s.get("content"), str) and s["content"].strip()]
            if not replies:
                return None
            step = replies[-1]
            n["text"] = step["content"]
            if len(n["text"]) > MAX_TEXT:
                return None
        n["turn"] = f"step-{step['step_index']}"
        stamp = step.get("created_at")
        if not isinstance(stamp, str) or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
            return None
        n["timestamp"] = stamp
    identity = canonical_json([n["sid"], n["cwd"]])
    return {"check": "antigravity_transcript", "identity_sha256": hashlib.sha256(identity.encode()).hexdigest()}


def capture_device(event: str, native: Any, device, gemini_home: Path) -> dict[str, Any]:
    token = arm_tailnet(device.core_url, event)
    try:
        return _capture_device(event, native, device, gemini_home)
    finally:
        disarm(token)


def _capture_device(event: str, native: Any, device, gemini_home: Path) -> dict[str, Any]:
    n = normalize(event, native)
    if n is None:
        return {"status": "ignored", "reason": "not_a_turn_boundary"}
    origin = session_origin(n, gemini_home)
    if origin is None:
        return {"status": "ignored", "reason": "unverified_session_origin", "native_session_id": n["sid"]}
    binding = bind_workspace(n, device, source=SOURCE)
    receipt = _publish(n, binding, origin, source=SOURCE, agent=AGENT)
    receipt["workspace"] = n["cwd"]
    return receipt


def work_record(native: dict[str, Any], device, receipt: dict[str, Any]) -> str | None:
    """ADR 0039 for one accepted event. Returns text to inject, or None."""
    event, sid = receipt.get("event_type"), receipt["native_session_id"]
    root = workspace_root(receipt["workspace"])
    if event == "session.started":
        receipt["worklog"] = worklog_client.recover(device, workspace_root=root, native_session_id=sid)
    if not device.memory_hint or event not in ("session.started", "user.prompt"):
        return None
    prompt = native.get("lastUserInput") if event == "user.prompt" else None
    resume, answer = worklog_client.resume_block(device, native_session_id=sid, workspace_root=root,
                                                 starting=event == "session.started", prompt=prompt,
                                                 start_seconds=memory_hint.BUDGET_SECONDS,
                                                 prompt_seconds=RECENT_SECONDS)
    if answer is not None:
        receipt["recent_activity"] = answer
    hint = None
    if event == "user.prompt":
        hint, receipt["memory_hint"] = memory_hint.request(device, project_id=receipt["project_id"],
                                                           native_session_id=sid, session_id=receipt["session_id"],
                                                           prompt=prompt)
    return "\n\n".join(t for t in (resume, hint) if t) or None


def reply(context: str | None) -> str:
    """What the hook prints. Never a decision; Stop must not ask to continue."""
    if not context:
        return "{}"
    return json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": context}}]}, ensure_ascii=False)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event", required=True, choices=EVENTS)
    p.add_argument("--device-binding", required=True, type=Path)
    p.add_argument("--receipts", required=True, type=Path)
    p.add_argument("--gemini-home", type=Path, default=Path.home() / ".gemini")
    args = p.parse_args()
    os.umask(0o077)
    native: Any = {}
    context = None
    budget = None
    try:
        native = json.load(sys.stdin)
        if args.event == "PreInvocation" and isinstance(native, dict) and native.get("invocationNum") != 0:
            print("{}")  # every model call runs this hook; only a turn's first one matters
            return 0
        device = load_device(args.device_binding)
        budget = arm_tailnet(device.core_url, args.event)
        receipt = capture_device(args.event, native, device, args.gemini_home)
        if receipt.get("status") == "accepted" and receipt.get("event_type"):
            try:
                context = work_record(native, device, receipt)
            except Exception as exc:
                receipt["work_record"] = {"status": "failed", "error_type": type(exc).__name__}
        receipt.pop("workspace", None)  # receipts hold ids, not paths
    except Exception as exc:  # no bearer, message text or raw HTTP body in stderr
        receipt = {"status": "failed", "agent": AGENT, "error_type": type(exc).__name__, "native_hook_event": args.event}
        print("Jasmine Antigravity capture failed; inspect private receipts.", file=sys.stderr)
    finally:
        disarm(budget)
    receipt["checked_at"] = datetime.now(timezone.utc).isoformat()
    if context:
        receipt["context_chars"] = len(context)
    append_receipt(args.receipts, receipt)
    print(reply(context))
    return 1 if receipt["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
