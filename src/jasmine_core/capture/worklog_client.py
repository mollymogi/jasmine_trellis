"""Hook side of the work record and the resume block (ADR 0039).

Capture writes a session Checkpoint with its system key at PreCompact and
SessionEnd, and asks Core to recover quiet sessions at SessionStart. The hint
handler reads the resume block with its `memory:recall`-only key. Every call
here fails open: a hook never blocks, and a receipt never carries text.
"""
from __future__ import annotations

import json
import fcntl
import os
import re
import subprocess
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Any

from ..api.client import CoreClient, hook_deadline
from ..hosthook.binding import TAILNET_CORE_ORIGIN, Binding, read_key
from ..hosthook.dialects._common import additional_context

MAX_DIRTY = 50
GIT_SECONDS = 0.5
#: A prompt this long is a task description or a paste, not a question about the past.
INTENT_MAX_CHARS = 200
#: After a resume block was injected, a prompt asking again within this window
#: gets nothing: the block is already in the context.
REINJECT_SECONDS = 30 * 60

_INTENT_RES = (
    re.compile(r"(刚才|刚刚|上次|之前|昨天|前面|上一次|最近).{0,6}(在)?(做|干|聊|弄|改|搞|进行|写|跑|处理)"),
    re.compile(r"记(不记)?得.{0,8}(刚才|刚刚|上次|之前|昨天|我们|我)"),
    re.compile(r"(接着|继续)(上次|刚才|之前|昨天)"),
    re.compile(r"(?i)\bwhat (was|were) (i|we) (doing|working on)\b"),
    re.compile(r"(?i)\bwhere (did|were) (we|i) (leave|stop)"),
    re.compile(r"(?i)\bdo you remember\b"),
    re.compile(r"(?i)\bpick up where\b"),
)
#: Only a short question counts for these: "进度" in a long instruction is not one.
_SHORT_INTENT_RES = (re.compile(r"做到哪"), re.compile(r"进度(?!条)(怎么样|如何|到哪|呢|$|[？?])"))


#: Words that widen the question to every workspace on this machine.
_GLOBAL_RE = re.compile(r"(所有|全部|别的|其他|其它|各个)的?(项目|工作区|目录|地方)|全局|跨项目"
                        r"|(?i:\b(all|other) (projects|workspaces)\b|\beverywhere\b)")
#: An agent named in the question narrows it to that agent's sessions.
_AGENT_RES = {"claude": re.compile(r"(?i)claude|克劳德"), "codex": re.compile(r"(?i)codex"),
              "grok": re.compile(r"(?i)grok"), "opencode": re.compile(r"(?i)open ?code"),
              "antigravity": re.compile(r"(?i)antigravity|反重力|\bagy\b")}


def recent_query(prompt: Any) -> dict[str, str] | None:
    """What a prompt asks about recent work: None, or the query options it implies.

    `{}` is the default (this workspace, any agent). "所有项目" / "全局" and the
    like add `scope: global`; naming exactly one agent adds `agent`. Naming two
    agents is not a filter, so neither is applied.
    """
    if not asks_about_recent_work(prompt):
        return None
    text = prompt.strip()
    query: dict[str, str] = {}
    if _GLOBAL_RE.search(text):
        query["scope"] = "global"
    named = [agent for agent, pattern in _AGENT_RES.items() if pattern.search(text)]
    if len(named) == 1:
        query["agent"] = named[0]
    return query


def asks_about_recent_work(prompt: Any) -> bool:
    """Whether the user's own words ask what was done recently."""
    if not isinstance(prompt, str):
        return False
    text = prompt.strip()
    if not text or text.startswith("/") or "<pasted_content" in text or len(text) > INTENT_MAX_CHARS:
        return False
    if any(pattern.search(text) for pattern in _INTENT_RES):
        return True
    return len(text) <= 30 and any(pattern.search(text) for pattern in _SHORT_INTENT_RES)


def workspace_state(root: str | Path, *, timeout: float = GIT_SECONDS) -> dict[str, Any]:
    """Branch, HEAD and changed files, from one bounded `git status`."""
    deadline = hook_deadline()
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return {"git": True, "complete": False}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v2", "--branch", "--untracked-files=normal"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=timeout,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return {"git": True, "complete": False}
    if proc.returncode != 0:
        return {"git": False}
    branch = head = None
    dirty: list[str] = []
    count = 0
    complete = True
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):]
        elif line.startswith("# branch.oid "):
            head = line[len("# branch.oid "):]
        elif line[:2] in ("1 ", "2 ", "u ", "? "):
            fields = {"1": 8, "2": 9, "u": 10, "?": 1}[line[0]]
            parts = line.split(" ", fields)
            path = parts[-1].split("\t")[0] if len(parts) > fields else ""
            count += 1
            if path and len(dirty) < MAX_DIRTY:
                dirty.append(path[:1024])
                if len(path) > 1024:
                    complete = False
            else:
                complete = False
    return {"git": True, "branch": branch, "head": head, "dirty": sorted(dirty), "dirty_count": count,
            "complete": complete}


def _git_env() -> dict[str, str]:
    """PATH only, so GIT_DIR and GIT_WORK_TREE cannot point git at another repo."""
    return {"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0"}


def git_remote_origin(root: str | Path, *, timeout: float = GIT_SECONDS) -> tuple[str | None, str]:
    """Raw `git remote get-url origin`. Absent only when git exits non-zero or prints nothing.

    A timeout, ``OSError``, or an origin ``classify_origin`` rejects raises. Callers
    must not post resolve in that case. The cap stays 0.5s and also stops at a hook deadline.
    """
    limit = timeout
    deadline = hook_deadline()
    if deadline is not None:
        limit = min(limit, deadline - time.monotonic())
    if limit <= 0:
        raise TimeoutError("git remote budget exhausted")
    proc = subprocess.run(
        ["git", "-C", str(root), "remote", "get-url", "origin"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        timeout=limit, env=_git_env())
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, "absent"
    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        return None, "absent"
    try:
        from ..workspace_alias import classify_origin
        kind, _key = classify_origin(text)
    except Exception as exc:
        raise ValueError("git origin is not classifiable") from exc
    if kind not in ("url", "local"):
        raise ValueError("git origin is not classifiable")
    return text, kind


def _system(binding: Binding, timeout: float) -> CoreClient:
    return CoreClient(binding.core_url, read_key(binding.api_key_file), timeout=timeout)


def record(binding: Binding, *, project_id: str, native_session_id: str, reason: str,
           workspace_root: str | Path) -> dict[str, Any]:
    """Write a session Checkpoint. Returns a receipt; never raises."""
    started = time.monotonic()
    try:
        answer = _system(binding, 3).post("/v1/session-checkpoints", {
            "host_id": binding.host_id, "project_id": project_id, "native_session_id": native_session_id,
            "reason": reason, "workspace_root": str(workspace_root),
            "workspace_state": workspace_state(workspace_root)})
        checkpoint = answer["session_checkpoint"]
        receipt = {"status": "recorded", "reason": reason, "replayed": answer["replayed"],
                   "session_checkpoint_id": checkpoint["session_checkpoint_id"],
                   "last_event_seq": checkpoint["last_event_seq"]}
    except Exception as exc:
        receipt = {"status": "failed", "reason": reason, "error_type": type(exc).__name__}
    receipt["latency_ms"] = int((time.monotonic() - started) * 1000)
    return receipt


def recover(binding: Binding, *, workspace_root: str | Path, native_session_id: str) -> dict[str, Any]:
    """Ask Core to record this workspace's quiet, unclosed sessions. Never raises."""
    started = time.monotonic()
    try:
        answer = _system(binding, 2).post("/v1/session-checkpoints/recover", {
            "host_id": binding.host_id, "workspace_root": str(workspace_root),
            "exclude_native_session_id": native_session_id})
        receipt = {"status": "ok", "recovered": answer.get("count", 0)}
    except Exception as exc:
        receipt = {"status": "failed", "error_type": type(exc).__name__}
    receipt["latency_ms"] = int((time.monotonic() - started) * 1000)
    return receipt


def recent(binding: Binding, *, workspace_root: str | Path, native_session_id: str,
           deadline: float, query: dict[str, str] | None = None) -> tuple[str | None, dict[str, Any]]:
    """The resume block, read with the Memory hint key. `(text, receipt)`; never raises."""
    started = time.monotonic()
    if binding.memory_key_file is None:
        return None, {"status": "disabled"}
    if binding.core_url == TAILNET_CORE_ORIGIN:
        from .hook_budget import RECENT_FLOOR_SECONDS
        wall = hook_deadline()
        # No clock, or less than the recent floor: do not start git or the POST.
        if wall is None or wall - time.monotonic() < RECENT_FLOOR_SECONDS:
            return None, {"status": "skipped", "reason": "no_time_left"}
        deadline = wall
    try:
        state = workspace_state(workspace_root, timeout=min(GIT_SECONDS, max(0.05, (deadline - started) / 3)))
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return None, {"status": "skipped", "reason": "no_time_left"}
        answer = CoreClient(binding.core_url, read_key(binding.memory_key_file), timeout=remaining).post(
            "/v1/continuity/recent", {"host_id": binding.host_id, "workspace_root": str(workspace_root),
                                      "exclude_native_session_id": native_session_id,
                                      "workspace_state": state, **(query or {})})
        text = answer.get("text")
        if not isinstance(text, str) or not text or answer.get("non_authoritative") is not True:
            text = None
        receipt = {"status": answer.get("status"), "chars": len(text) if text else 0,
                   "sessions": [s.get("session_id") for s in answer.get("sessions") or []],
                   **(query or {})}
    except Exception as exc:
        text, receipt = None, {"status": "failed", "error_type": type(exc).__name__}
    if text is not None and time.monotonic() > deadline:
        text, receipt = None, {**receipt, "status": "late"}
    receipt["latency_ms"] = int((time.monotonic() - started) * 1000)
    return text, receipt


# -- once per session --------------------------------------------------------


def _marker(spool: Path, native_session_id: str) -> Path:
    return spool / "recent-activity" / (native_session_id + ".json")


def injected_recently(spool: Path, native_session_id: str, *, now: float | None = None) -> bool:
    path = _marker(spool, native_session_id)
    try:
        if path.is_symlink():
            return True  # refuse to reason about a planted marker; inject nothing
        stamp = json.loads(path.read_text())["at"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (now if now is not None else time.time()) - float(stamp) < REINJECT_SECONDS


def mark_injected(spool: Path, native_session_id: str, trigger: str) -> None:
    path = _marker(spool, native_session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump({"at": time.time(), "trigger": trigger}, handle)
    except OSError:
        pass


def resume_block(binding: Binding, *, native_session_id: str, workspace_root: str | Path, starting: bool,
                 prompt: Any, start_seconds: float, prompt_seconds: float) -> tuple[str | None, dict[str, Any] | None]:
    """The resume block for a session that is starting or a prompt that asks for it.

    `(text, receipt)`; `(None, None)` when neither applies. The default question
    is answered once per session; a question naming all projects or an agent is
    a new question and is always answered. Never raises.
    """
    query = None if starting else recent_query(prompt)
    if not starting and query is None:
        return None, None
    trigger, seconds = ("session_start", start_seconds) if starting else ("prompt", prompt_seconds)
    if not query and injected_recently(binding.spool_dir, native_session_id):
        return None, {"status": "skipped", "reason": "already_injected", "trigger": trigger}
    text, answer = recent(binding, workspace_root=workspace_root, native_session_id=native_session_id,
                          deadline=time.monotonic() + seconds, query=query)
    if text and not query:
        mark_injected(binding.spool_dir, native_session_id, trigger)
    return text, {**answer, "trigger": trigger}


# -- deferred delivery ---------------------------------------------------------
#
# Grok discards what its SessionStart and UserPromptSubmit hooks print; only a
# tool hook's `additionalContext` reaches the model. The block is staged here
# and handed over on the session's next tool call.

#: A staged block older than this is dropped instead of delivered.
STAGED_SECONDS = 15 * 60
_SAFE_SID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _staged(spool: Path, native_session_id: str) -> Path:
    if not _SAFE_SID.match(native_session_id or ""):
        raise ValueError("unsafe native session id")
    return spool / "staged-context" / (native_session_id + ".json")


#: Staged parts, in the order they are delivered.
STAGED_PARTS = ("resume", "hint")


def _read_staged(path: Path, now: float) -> dict[str, str]:
    """Fresh staged parts at `path`; {} for none, a stale file or anything unreadable."""
    try:
        if path.is_symlink():
            path.unlink()
            return {}
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as handle:
            body = json.load(handle)
        parts = body["parts"]
        if now - float(body["at"]) >= STAGED_SECONDS or not isinstance(parts, dict):
            return {}
        return {k: v for k, v in parts.items() if k in STAGED_PARTS and isinstance(v, str) and v}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


@contextmanager
def _staged_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Keep this inode: unlinking a lock lets later callers lock a different file.
    fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def stage(spool: Path, native_session_id: str, **parts: str | None) -> bool:
    """Keep parts for the session's next tool call. A new part replaces the same
    kind already staged (a newer hint replaces an older one); others are kept. Never raises."""
    parts = {k: v for k, v in parts.items() if k in STAGED_PARTS and v}
    if not parts:
        return False
    try:
        path = _staged(spool, native_session_id)
        with _staged_lock(path):
            merged = {**_read_staged(path, time.time()), **parts}
            temp = path.with_name("." + path.name + ".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump({"at": time.time(), "parts": merged}, handle, ensure_ascii=False)
            os.replace(temp, path)
        return True
    except (OSError, ValueError):
        return False


def take_staged(spool: Path, native_session_id: str, *, now: float | None = None) -> str | None:
    """The staged text, removed so it is delivered once; None when there is none. Never raises."""
    try:
        path = _staged(spool, native_session_id)
        with _staged_lock(path):
            parts = _read_staged(path, now if now is not None else time.time())
            path.unlink(missing_ok=True)
            return "\n\n".join(parts[k] for k in STAGED_PARTS if k in parts) or None
    except (OSError, ValueError):
        return None


def envelope(hook_event: str, text: str) -> str:
    return additional_context(hook_event, text)
