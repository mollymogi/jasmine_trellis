"""Opt-in native Codex conversation capture through existing Core APIs.

Project-local lifecycle hooks register a Core Session and attach verbatim native
prompts/last assistant messages to it. This records conversations, not Memory:
only the separately started Memory worker may retain eligible committed sources.
No transcript scraping, inferred assistant messages or keyword approval occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from ..api.client import CoreClient
from ..canonical import canonical_json
from ..evidence import _deterministic_event_id
from ..hosthook.binding import Binding, load_binding, read_key

SOURCE = "codex-native-conversation-v1"
EVENTS = {"SessionStart": "session.started", "UserPromptSubmit": "user.prompt",
          "Stop": "assistant.message", "SessionEnd": "session.ended"}


def session_origin(native: dict[str, Any]) -> dict[str, str] | None:
    """Verify a persisted main-session header; never read transcript messages.

    Desktop suggestion forks have transcript_path=null and no origin flags in
    UserPromptSubmit. Their hook name alone is insufficient human provenance.
    Unknown/ephemeral inputs fail closed, including Stop, so a generated answer
    cannot leak back as an ordinary conversation message. Persisted forks remain
    eligible. This is an opt-in local boundary, not authentication of a human.
    """
    path = native.get("transcript_path")
    if not isinstance(path, str) or not Path(path).is_absolute():
        return None
    try:
        with Path(path).open("rb") as stream:
            line = stream.readline(1024 * 1024 + 1)
        if len(line) > 1024 * 1024:
            return None
        header = json.loads(line)
        meta = header.get("payload", {})
        if (header.get("type") != "session_meta"
                or meta.get("id") != native.get("session_id")
                or not isinstance(meta.get("cwd"), str)
                or not isinstance(native.get("cwd"), str)
                or Path(meta["cwd"]).resolve() != Path(native["cwd"]).resolve()
                or meta.get("source") not in {"cli", "exec", "vscode"}):
            return None
        return {"check": "persisted_session_header", "session_source": meta["source"],
                "header_sha256": hashlib.sha256(line).hexdigest()}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def capture(native: dict[str, Any], binding: Binding) -> dict[str, Any]:
    """Return a receipt of accepted Core writes; exceptions are never success."""
    event = native.get("hook_event_name")
    if event not in EVENTS or native.get("subagent_type") or native.get("subagent_id"):
        return {"status": "ignored"}
    sid = native.get("session_id")
    if not isinstance(sid, str) or not sid or not binding.project_id:
        raise ValueError("native session_id and explicit project binding are required")
    if event == "Stop" and (native.get("stop_hook_active") or not isinstance(native.get("last_assistant_message"), str)):
        return {"status": "ignored", "reason": "no_native_assistant_message"}
    text = native.get("prompt") if event == "UserPromptSubmit" else native.get("last_assistant_message") if event == "Stop" else ""
    if not isinstance(text, str):
        raise ValueError("native prompt must be text")
    if event == "UserPromptSubmit" and binding.human_api_key_file is None:
        raise ValueError("a separate human prompt key is required")
    origin = session_origin(native)
    if origin is None:
        return {"status": "ignored", "reason": "unverified_session_origin",
                "native_hook_event": event, "native_session_id": sid,
                "native_turn_id": native.get("turn_id")}
    system = CoreClient(binding.core_url, read_key(binding.api_key_file), timeout=5)
    # Stable source identity makes registration idempotent across start/resume,
    # retries, and concurrent hooks. The Core, not the hook, assigns ses_ IDs.
    identity = canonical_json([SOURCE, binding.host_id, binding.project_id, binding.task_id, sid])
    session = system.post("/v1/sessions", {
        "title": "Codex " + sid, "project_id": binding.project_id,
        "task_id": binding.task_id, "host_id": binding.host_id,
        "source_system": SOURCE + "-registration", "source_event_id": identity,
        "event_id": _deterministic_event_id(SOURCE, identity, "registration"),
    })["object"]["session_id"]
    payload = {"text": text, "native_session_id": sid, "native_turn_id": native.get("turn_id"),
               "native_hook_event": event, "trust": "NATIVE_HOOK", "origin": origin}
    if event == "SessionStart":
        payload["source"] = native.get("source")
    if event == "SessionEnd":
        payload["reason"] = native.get("reason")
    source_id = hashlib.sha256(canonical_json([identity, event, payload]).encode()).hexdigest()
    client = CoreClient(binding.core_url, read_key(binding.human_api_key_file), timeout=5) if event == "UserPromptSubmit" else system
    response = client.post("/v1/events", {
        "event_type": EVENTS[event], "source_system": SOURCE, "source_event_id": source_id,
        "event_id": _deterministic_event_id(SOURCE, source_id),
        "host_id": binding.host_id, "project_id": binding.project_id,
        "task_id": binding.task_id, "session_id": session, "payload": payload,
    })
    recorded = response["event"]
    return {"status": "accepted", "native_hook_event": event, "native_session_id": sid,
            "native_turn_id": native.get("turn_id"), "session_id": session,
            "event_id": recorded["event_id"], "event_type": recorded["event_type"],
            "origin": origin, "replayed": response["replayed"], "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def append_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(receipt, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--exact-root", action="store_true", help="capture this directory only, excluding nested projects")
    parser.add_argument("--receipts", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    receipt: dict[str, Any] = {"status": "failed"}
    native: dict[str, Any] = {}
    try:
        parsed = json.load(sys.stdin)
        if not isinstance(parsed, dict):
            raise ValueError("native event must be an object")
        native = parsed
        # An explicit isolated directory boundary prevents inherited hooks from
        # capturing sessions from a different project. Symlinks resolve first.
        root = args.project_root.resolve(strict=True)
        cwd = Path(str(native.get("cwd", "")))
        if not cwd.is_absolute() or not cwd.resolve().is_relative_to(root) or args.exact_root and cwd.resolve() != root:
            receipt = {"status": "ignored", "reason": "outside_project"}
        else:
            receipt = capture(native, load_binding(args.binding))
    except Exception as exc:  # no bearer, message text or raw HTTP body in stderr
        receipt = {"status": "failed", "error_type": type(exc).__name__,
                   "native_hook_event": native.get("hook_event_name"),
                   "native_session_id": native.get("session_id")}
        print("Jasmine isolated conversation capture failed; inspect private receipts.", file=sys.stderr)
    append_receipt(args.receipts, receipt)
    # Stop must never request a continuation; capture failure remains visible.
    if native.get("hook_event_name") == "Stop":
        print("{}")
    return 1 if receipt["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
