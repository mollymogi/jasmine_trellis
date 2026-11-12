"""Opt-in native Codex conversation capture through existing Core APIs.

Project-local lifecycle hooks register a Core Session and attach verbatim native
prompts/last assistant messages to it. This records conversations, not Memory:
only the separately started Memory worker may retain eligible committed sources.
No transcript scraping, inferred assistant messages or keyword approval occurs.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .. import ids
from ..api.client import ApiError, CoreClient
from ..canonical import canonical_json
from ..evidence import _deterministic_event_id
from ..hosthook.binding import TAILNET_CORE_ORIGIN, Binding, load_binding, load_device, read_key
from . import memory_hint, worklog_client
from .hook_budget import arm_tailnet, disarm

SOURCE = "codex-native-conversation-v1"
EVENTS = {"SessionStart": "session.started", "UserPromptSubmit": "user.prompt",
          "Stop": "assistant.message", "SessionEnd": "session.ended"}

PROJECT_SOURCE = SOURCE + "-workspace"
#: Of the hint budget, the most the resume block may take on a prompt that asks for it.
RECENT_SECONDS = 0.8


def workspace_root(cwd: str) -> Path:
    """Nearest Git worktree root, or the launch directory for a non-Git project.

    Resolve aliases before deriving identity. Merely inspect the .git marker:
    do not run repository commands or write configuration inside user projects.
    Worktrees and nested repositories are separate workspaces.
    """
    path = Path(cwd)
    if not path.is_absolute():
        raise ValueError("native cwd must be absolute")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("native cwd must be a directory")
    for parent in (path, *path.parents):
        if (parent / ".git").is_dir() or (parent / ".git").is_file():
            return parent
    return path


def _bind_timeout(tailnet: bool, started: float, timeout: float) -> float:
    if tailnet:
        return timeout
    left = timeout - (time.monotonic() - started)
    if left <= 0:
        raise TimeoutError("hook budget exhausted")
    return left


def bind_workspace(native: dict[str, Any], device: Binding, source: str = SOURCE, *, timeout: float = 2) -> Binding:
    """Register ownership through Core's atomic, idempotent object API.

    No project-supplied binding or environment variable can redirect the
    device's destination/credentials or silently attach a conversation to a Task.
    The tailnet origin adopts ``project_id`` only from ``status=ok`` resolve and
    never posts ``/v1/projects``. Loopback with no confirmed alias still does.
    """
    root = workspace_root(native["cwd"])
    origin, kind = worklog_client.git_remote_origin(root)
    tailnet = device.core_url == TAILNET_CORE_ORIGIN
    started = time.monotonic()
    answer: dict[str, Any] | None
    try:
        answer = CoreClient(device.core_url, read_key(device.api_key_file),
                            timeout=_bind_timeout(tailnet, started, timeout)).post(
            "/v1/workspaces/resolve", {
                "host_id": device.host_id, "root": str(root), "origin": origin, "remote_kind": kind})
    except ApiError as exc:
        if tailnet or exc.status == 409 or exc.code == "alias_conflict":
            raise
        answer = None
    except (TimeoutError, OSError):
        if tailnet:
            raise
        answer = None
    status = answer.get("status") if isinstance(answer, dict) else None
    project_id = answer.get("project_id") if isinstance(answer, dict) else None
    if status == "ok":
        if (not isinstance(answer, dict) or answer.get("alias_conflict") is True
                or not isinstance(project_id, str) or not ids.is_id(project_id, "prj")):
            raise ValueError("workspace not confirmed")
        return replace(device, project_id=project_id, task_id=None, step_id=None, inject_context=False)
    if tailnet or status not in (None, "confirmation_required"):
        raise ValueError("workspace not confirmed")
    workspace_source = PROJECT_SOURCE if source == SOURCE else source + "-workspace"
    identity = canonical_json([workspace_source, device.host_id, str(root)])
    project = CoreClient(device.core_url, read_key(device.api_key_file),
                         timeout=_bind_timeout(tailnet, started, timeout)).post("/v1/projects", {
        "name": root.name or str(root), "description": str(root), "host_id": device.host_id,
        "source_system": workspace_source, "source_event_id": identity,
        "event_id": _deterministic_event_id(workspace_source, identity),
    })["object"]["project_id"]
    return replace(device, project_id=project, task_id=None, step_id=None, inject_context=False)


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
            "project_id": binding.project_id,
            "event_id": recorded["event_id"], "event_type": recorded["event_type"],
            "origin": origin, "replayed": response["replayed"], "text_sha256": hashlib.sha256(text.encode()).hexdigest()}


def capture_device(native: dict[str, Any], device: Binding) -> dict[str, Any]:
    token = arm_tailnet(device.core_url, native.get("hook_event_name") if isinstance(native, dict) else None)
    try:
        return _capture_device(native, device)
    finally:
        disarm(token)


def _capture_device(native: dict[str, Any], device: Binding) -> dict[str, Any]:
    # Check provenance before even registering a project. Prediction forks and
    # unsupported/subagent events must leave no projection behind.
    if native.get("hook_event_name") not in EVENTS or native.get("subagent_id") or native.get("subagent_type"):
        return {"status": "ignored"}
    if session_origin(native) is None:
        return {"status": "ignored", "reason": "unverified_session_origin"}
    if native.get("hook_event_name") == "Stop" and (native.get("stop_hook_active") or not isinstance(native.get("last_assistant_message"), str)):
        return {"status": "ignored", "reason": "no_native_assistant_message"}
    return capture(native, bind_workspace(native, device))


def append_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(receipt, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--binding", type=Path)
    mode.add_argument("--device-binding", type=Path, help="opt-in user-wide capture with automatic workspace registration")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--exact-root", action="store_true", help="capture this directory only, excluding nested projects")
    parser.add_argument("--receipts", required=True, type=Path)
    args = parser.parse_args()
    if args.binding and args.project_root is None:
        parser.error("--binding requires --project-root")
    if args.device_binding and (args.project_root or args.exact_root):
        parser.error("device capture does not accept a fixed project boundary")
    os.umask(0o077)
    receipt: dict[str, Any] = {"status": "failed"}
    native: dict[str, Any] = {}
    binding: Binding | None = None
    hint: str | None = None
    budget = None
    try:
        parsed = json.load(sys.stdin)
        if not isinstance(parsed, dict):
            raise ValueError("native event must be an object")
        native = parsed
        # An explicit isolated directory boundary prevents inherited hooks from
        # capturing sessions from a different project. Symlinks resolve first.
        if args.device_binding:
            binding = load_device(args.device_binding)
            budget = arm_tailnet(binding.core_url, native.get("hook_event_name"))
            receipt = capture_device(native, binding)
        else:
            root = args.project_root.resolve(strict=True)
            cwd = Path(str(native.get("cwd", "")))
            if not cwd.is_absolute() or not cwd.resolve().is_relative_to(root) or args.exact_root and cwd.resolve() != root:
                receipt = {"status": "ignored", "reason": "outside_project"}
            else:
                binding = load_binding(args.binding)
                budget = arm_tailnet(binding.core_url, native.get("hook_event_name"))
                receipt = capture(native, binding)
        # ADR 0039: the work record. SessionEnd closes this session's; SessionStart
        # records the workspace's earlier sessions that never sent one.
        if (binding is not None and args.device_binding and receipt.get("status") == "accepted"
                and receipt.get("event_type") in ("session.ended", "session.started")):
            root = workspace_root(native["cwd"])
            if receipt["event_type"] == "session.ended":
                receipt["worklog"] = worklog_client.record(
                    binding, project_id=receipt["project_id"], native_session_id=receipt["native_session_id"],
                    reason="SESSION_END", workspace_root=root)
            else:
                receipt["worklog"] = worklog_client.recover(
                    binding, workspace_root=root, native_session_id=receipt["native_session_id"])
        # ADR 0039 B: the resume block. A new Codex session in the same workspace
        # learns what was last done there, by Codex or any other agent; so does a
        # prompt that asks about recent work. Same opt-in and key as the hint.
        recent = None
        if (binding is not None and args.device_binding and binding.memory_hint
                and receipt.get("status") == "accepted"):
            starting = receipt.get("event_type") == "session.started" and native.get("source") in ("startup", "clear")
            asks = receipt.get("event_type") == "user.prompt" and worklog_client.recent_query(native.get("prompt")) is not None
            if starting or asks:
                recent, answer = worklog_client.resume_block(
                    binding, native_session_id=receipt["native_session_id"],
                    workspace_root=workspace_root(native["cwd"]), starting=starting,
                    prompt=native.get("prompt"), start_seconds=memory_hint.BUDGET_SECONDS,
                    prompt_seconds=RECENT_SECONDS)
                if answer is not None:
                    receipt["recent_activity"] = answer
        # ADR 0036: only a prompt Core has just accepted as the user's may get a
        # Memory hint, and only when this device opted in. Never raises.
        if (binding is not None and binding.memory_hint and receipt.get("status") == "accepted"
                and receipt.get("event_type") == "user.prompt"):
            hint, receipt["memory_hint"] = memory_hint.request(
                binding, project_id=receipt["project_id"], native_session_id=receipt["native_session_id"],
                session_id=receipt["session_id"], prompt=native["prompt"])
        hint = "\n\n".join(t for t in (recent, hint) if t) or None
    except Exception as exc:  # no bearer, message text or raw HTTP body in stderr
        receipt = {"status": "failed", "error_type": type(exc).__name__,
                   "native_hook_event": native.get("hook_event_name"),
                   "native_session_id": native.get("session_id")}
        print("Jasmine conversation capture failed; inspect private receipts.", file=sys.stderr)
    finally:
        disarm(budget)
    append_receipt(args.receipts, receipt)
    # Stop must never request a continuation; capture failure remains visible.
    if native.get("hook_event_name") == "Stop":
        print("{}")
    elif hint is not None:
        event = "SessionStart" if native.get("hook_event_name") == "SessionStart" else "UserPromptSubmit"
        print(worklog_client.envelope(event, hint))
    return 1 if receipt["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
