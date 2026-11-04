"""Work record and recent-activity resume (ADR 0039 A and B).

Core already holds every captured session's prompts and replies. This module
turns them into two things:

* a **session Checkpoint**: an immutable, dated record of what one session did
  and what the workspace looked like then, written at PreCompact/SessionEnd or
  recovered by the next session when the agent closed without saying so;
* a **resume block**: a short, non-authoritative text that tells a new session
  what was last done in the same workspace, by any agent.

Nothing here calls a model, and nothing is inferred: every line of a record is
an Event Core already stored, clipped and redacted. Sessions are grouped by
*workspace family*: one directory, whichever agent registered it, with a git
worktree counted as part of the repository it belongs to.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import canonical, clock, db, errors, ids
from .events import EventStore
from .models import NewEvent

REASONS = ("PRE_COMPACT", "SESSION_END", "RECOVERED")
SOURCE_SYSTEM = "core-worklog"
#: A resume block shows sessions this recent, and no older.
WINDOW = timedelta(days=7)
#: A session with no activity for this long, and no closing record, is recovered.
IDLE = timedelta(minutes=10)
RECOVER_LIMIT = 5
BUDGET = 1500
PROMPT_CHARS = 160
REPLY_CHARS = 400
KEEP_PROMPTS = 8
SHOW_PROMPTS = 5
MAX_DIRTY = 50
MAX_ROOT_CHARS = 4096

_PASTED_RE = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content>", re.S)
_FILES_RE = re.compile(r"#\s*Files mentioned by the user:.*?(?=\n\S|\Z)", re.S)
#: An agent wraps UI state, question replies and browser context in tags. They
#: are not what the user said, so the block becomes a placeholder.
_TAG_BLOCK_RE = re.compile(r"<([A-Za-z][\w-]*)\b[^>]*>.*?</\1>", re.S)
_TAG_RE = re.compile(r"</?[A-Za-z][\w-]*\b[^>]*>")
_PLACEHOLDER_RE = re.compile(r"\[(粘贴内容|附件|附加内容)\]")
_SPACE_RE = re.compile(r"\s+")


# -- workspace families --------------------------------------------------------

_FAMILY_CACHE: dict[str, str] = {}


def family_root(root: str) -> str:
    """The root a workspace is grouped under: a worktree's repository, else itself.

    Read from the worktree's `.git` file (`gitdir: <repo>/.git/worktrees/<name>`)
    rather than by running git, so it costs one small read and cannot hang.
    """
    if root in _FAMILY_CACHE:
        return _FAMILY_CACHE[root]
    family = root
    marker = Path(root) / ".git"
    try:
        if marker.is_file() and not marker.is_symlink():
            line = marker.read_text(encoding="utf-8", errors="replace")[:4096].strip()
            if line.startswith("gitdir:"):
                gitdir = Path(line[len("gitdir:"):].strip())
                if not gitdir.is_absolute():
                    gitdir = (Path(root) / gitdir).resolve()
                parts = gitdir.parts
                if len(parts) >= 3 and parts[-2] == "worktrees" and parts[-3] == ".git":
                    family = str(Path(*parts[:-3]))
    except OSError:
        family = root
    _FAMILY_CACHE[root] = family
    return family


#: How far up or down a nearby workspace may be and still count. A non-git
#: folder that holds a repository ("Jasmine Mesh" around "jasmine-core-v1") is
#: worked on together with it; three levels up is someone's whole projects tree.
NEARBY_LEVELS = 2


def related(root: str, family: str) -> bool:
    """Whether a session rooted at `root` belongs to the resume block of `family`.

    Its own family, or a folder up to NEARBY_LEVELS above or below it. The home
    directory and its parents never count as an ancestor, so a session started
    in `~` or `/` is never everyone's previous work.
    """
    other = family_root(root)
    if other == family:
        return True
    near, far = Path(other), Path(family)
    for upper, lower, is_ancestor in ((near, far, True), (far, near, False)):
        try:
            depth = len(lower.relative_to(upper).parts)
        except ValueError:
            continue
        if not 1 <= depth <= NEARBY_LEVELS:
            return False
        if is_ancestor:
            home = Path.home()
            return upper != home and upper not in home.parents and len(upper.parts) > 2
        return True
    return False


def _require_root(value: Any, field: str = "workspace_root") -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > MAX_ROOT_CHARS or "\x00" in value:
        raise errors.InvalidRequest(f"{field} must be an absolute path", field=field)
    return value


# -- workspace state (reported by the hook, compared here) ---------------------


def _require_state(value: Any) -> dict[str, Any] | None:
    """A hook-reported git state, or None. Malformed input is refused, not guessed."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise errors.InvalidRequest("workspace_state must be an object", field="workspace_state")
    allowed = {"git", "branch", "head", "dirty", "dirty_count", "complete"}
    if set(value) - allowed:
        raise errors.InvalidRequest("unknown workspace_state fields", field="workspace_state")
    if value.get("git") is not True:
        return {"git": False}
    for name in ("branch", "head"):
        if value.get(name) is not None and (not isinstance(value[name], str) or len(value[name]) > 256):
            raise errors.InvalidRequest(f"workspace_state.{name} must be short text", field="workspace_state")
    dirty = value.get("dirty", [])
    if (not isinstance(dirty, list) or len(dirty) > MAX_DIRTY
            or not all(isinstance(p, str) and len(p) <= 1024 for p in dirty)):
        raise errors.InvalidRequest("workspace_state.dirty must be at most 50 paths", field="workspace_state")
    count = value.get("dirty_count", len(dirty))
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise errors.InvalidRequest("workspace_state.dirty_count must be a count", field="workspace_state")
    return {"git": True, "branch": value.get("branch"), "head": value.get("head"),
            "dirty": list(dirty), "dirty_count": count, "complete": value.get("complete") is True}


def compare_states(then: dict[str, Any] | None, now: dict[str, Any] | None) -> str | None:
    """One line on how the workspace changed since a record, or None if unknown."""
    if not then or not now or not then.get("git") or not now.get("git"):
        return None
    if not then.get("complete") or not now.get("complete"):
        return "未能确认（状态扫描不完整）"
    parts = []
    if then.get("branch") != now.get("branch"):
        parts.append(f"分支 {then.get('branch')} → {now.get('branch')}")
    if then.get("head") != now.get("head"):
        parts.append(f"HEAD {str(then.get('head'))[:7]} → {str(now.get('head'))[:7]}")
    before, after = set(then.get("dirty") or []), set(now.get("dirty") or [])
    if then.get("dirty_count") != now.get("dirty_count") or before != after:
        change = f"未提交改动 {then.get('dirty_count')} → {now.get('dirty_count')} 个文件"
        added = sorted(after - before)[:5]
        gone = sorted(before - after)[:5]
        if added:
            change += f"（新增 {', '.join(added)}）"
        if gone:
            change += f"（不再改动 {', '.join(gone)}）"
        parts.append(change)
    return "；".join(parts) if parts else "已观察的 Git 元数据一致（未比较文件内容）"


# -- session records -------------------------------------------------------------


#: Query scopes for `recent`: this workspace's family (the default), or every
#: workspace on this host, newest first. Agents filter by the source of a session's
#: events, so a Codex session is found whatever directory it was opened in.
SCOPES = ("workspace", "global")
AGENTS = ("claude", "codex", "grok")
AGENT_SOURCE_PREFIX = {"claude": "claude-", "codex": "codex-", "grok": "grok-"}


def _require_scope(value: Any) -> str:
    if value is None:
        return "workspace"
    if value not in SCOPES:
        raise errors.InvalidRequest(f"scope must be one of {', '.join(SCOPES)}", field="scope")
    return value


def _require_agent(value: Any) -> str | None:
    if value is None:
        return None
    if value not in AGENTS:
        raise errors.InvalidRequest(f"agent must be one of {', '.join(AGENTS)}", field="agent")
    return value


def _agent(source_system: str) -> str:
    for name in ("claude", "codex", "grok"):
        if source_system.startswith(name):
            return name
    return "agent"


def _clean(text: str, limit: int) -> str:
    text = _PASTED_RE.sub(" [粘贴内容] ", text)
    text = _FILES_RE.sub(" [附件] ", text)
    text = _TAG_BLOCK_RE.sub(" [附加内容] ", text)
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = _SPACE_RE.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        return clock.parse_rfc3339(stamp)
    except ValueError:
        return None


def session_record(conn, session_id: str) -> dict[str, Any] | None:
    """What one session did, from its own Events. None for a session with no turns."""
    rows = conn.execute(
        "SELECT seq, event_id, event_type, source_system, occurred_at, payload_json FROM events"
        " WHERE session_id=? AND event_type IN ('session.started','user.prompt','assistant.message','session.ended')"
        " ORDER BY seq", (session_id,)).fetchall()
    prompts: list[dict[str, Any]] = []
    reply = None
    native = None
    agent = None
    started = last = ended = None
    last_seq = 0
    for row in rows:
        payload = json.loads(row["payload_json"])
        native = native or payload.get("native_session_id")
        agent = agent or _agent(row["source_system"])
        started = started or row["occurred_at"]
        last_seq = max(last_seq, row["seq"])
        if row["event_type"] == "user.prompt":
            text = _clean(payload.get("text") or "", PROMPT_CHARS)
            # A prompt that was only attachments or UI state says nothing to resume from.
            if _PLACEHOLDER_RE.sub("", text).strip():
                prompts.append({"at": row["occurred_at"], "text": text})
            last = row["occurred_at"]
        elif row["event_type"] == "assistant.message":
            reply = payload.get("text") or reply
            last = row["occurred_at"]
        elif row["event_type"] == "session.ended":
            ended = row["occurred_at"]
    if not prompts:
        return None
    # The newest tasks: in a long session the first ones are long settled.
    tasks = [r["title"] for r in conn.execute(
        "SELECT t.title, MAX(t.created_at) AS at FROM resolution_source_bindings b JOIN tasks t ON t.task_id=b.task_id"
        " WHERE b.source_event_id IN (SELECT event_id FROM events WHERE session_id=? AND event_type='user.prompt')"
        " GROUP BY t.title ORDER BY at DESC LIMIT 8", (session_id,)).fetchall()][::-1]
    return {
        "session_id": session_id, "native_session_id": native, "agent": agent or "agent",
        "started_at": started, "last_activity_at": last, "ended_at": ended,
        "turns": len(prompts), "last_event_seq": last_seq,
        "prompts": prompts[-KEEP_PROMPTS:],
        "tasks": [_clean(t, 80) for t in tasks if t],
        "last_reply": _clean(reply, REPLY_CHARS) if reply else None,
    }


def _redacted(value: Any) -> Any:
    if isinstance(value, str):
        return canonical.redact_text(value)
    if isinstance(value, list):
        return [_redacted(v) for v in value]
    if isinstance(value, dict):
        return {k: _redacted(v) for k, v in value.items()}
    return value


# -- store -------------------------------------------------------------------------


class WorklogStore:
    def __init__(self, conn, *, schema_version: int) -> None:
        self.conn = conn
        self.events = EventStore(conn, schema_version=schema_version)

    # writing

    def record(self, body: dict[str, Any], *, actor_id: str, actor_kind: str) -> dict[str, Any]:
        """Write one session Checkpoint, or return the one that already covers it."""
        allowed = {"host_id", "project_id", "native_session_id", "reason", "workspace_root", "workspace_state"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
        reason = body.get("reason")
        if reason not in ("PRE_COMPACT", "SESSION_END"):
            raise errors.InvalidRequest("reason must be PRE_COMPACT or SESSION_END", field="reason")
        host_id, project_id = body.get("host_id"), body.get("project_id")
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
        if not ids.is_id(project_id, "prj"):
            raise errors.InvalidRequest("project_id must be a prj_ id", field="project_id")
        native = body.get("native_session_id")
        if not isinstance(native, str) or not 1 <= len(native) <= 256:
            raise errors.InvalidRequest("native_session_id must be text of 1..256 characters",
                                        field="native_session_id")
        root = _require_root(body.get("workspace_root"))
        state = _require_state(body.get("workspace_state"))
        with db.transaction(self.conn):
            session_id = self._session_for(project_id, host_id, native)
            if session_id is None:
                raise errors.NotFound("session", native)
            return self._write(session_id, project_id, host_id, reason, root, state,
                               actor_id=actor_id, actor_kind=actor_kind)

    def recover(self, body: dict[str, Any], *, actor_id: str, actor_kind: str,
                now: datetime | None = None) -> dict[str, Any]:
        """Record sessions of this workspace that went quiet without a closing record."""
        allowed = {"host_id", "workspace_root", "exclude_native_session_id"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
        host_id = body.get("host_id")
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
        family = family_root(_require_root(body.get("workspace_root")))
        exclude = body.get("exclude_native_session_id")
        now = now or clock.now()
        written = []
        with db.transaction(self.conn):
            # Covered, active or excluded sessions must not hide older eligible
            # sessions. Only successful recoveries count toward RECOVER_LIMIT.
            for candidate in self._candidates(family, host_id, now, limit=None):
                if len(written) >= RECOVER_LIMIT:
                    break
                last = _parse(candidate["last_at"])
                if last is None or now - last < IDLE:
                    continue  # possibly still running; it will close or be recovered later
                covered = self.conn.execute(
                    "SELECT MAX(last_event_seq) AS seq FROM session_checkpoints WHERE session_id=?",
                    (candidate["session_id"],)).fetchone()["seq"] or 0
                if covered >= candidate["last_seq"]:
                    continue
                result = self._write(candidate["session_id"], candidate["project_id"], host_id, "RECOVERED",
                                     candidate["root"], None, actor_id=actor_id, actor_kind=actor_kind,
                                     exclude_native=exclude)
                if result is not None and not result["replayed"]:
                    written.append(result["session_checkpoint"]["session_checkpoint_id"])
        return {"recovered": written, "count": len(written)}

    def _write(self, session_id, project_id, host_id, reason, root, state, *, actor_id, actor_kind,
               exclude_native=None):
        record = session_record(self.conn, session_id)
        if record is None:
            if reason == "RECOVERED":
                return None
            raise errors.InvalidRequest("the session has no captured turns yet", field="native_session_id")
        if exclude_native and record["native_session_id"] == exclude_native:
            return None
        existing = self.conn.execute(
            "SELECT * FROM session_checkpoints WHERE session_id=? AND reason=? AND last_event_seq=?",
            (session_id, reason, record["last_event_seq"])).fetchone()
        if existing is not None:
            return {"session_checkpoint": _row(existing), "replayed": True}
        record = _redacted({**record, "workspace_state": state})
        raw = canonical.canonical_json(record)
        digest = canonical.sha256_hex(raw)
        ident = ids.new_id("sck")
        event, _ = self.events.append(NewEvent(
            event_type="session.checkpointed", source_system=SOURCE_SYSTEM, actor_id=actor_id,
            actor_kind=actor_kind, host_id=host_id, project_id=project_id, session_id=session_id,
            occurred_at=clock.now(),
            payload={"text": "", "session_checkpoint_id": ident, "reason": reason,
                     "record_sha256": digest, "last_event_seq": record["last_event_seq"]}))
        self.conn.execute(
            "INSERT INTO session_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, event["event_id"], session_id, project_id, host_id, actor_id, record["native_session_id"],
             record["agent"], reason, root, family_root(root), record["last_event_seq"], record["turns"],
             record["started_at"], record["last_activity_at"], record["ended_at"], raw, digest,
             clock.now_rfc3339()))
        row = self.conn.execute("SELECT * FROM session_checkpoints WHERE session_checkpoint_id=?",
                                (ident,)).fetchone()
        return {"session_checkpoint": _row(row), "replayed": False}

    def _session_for(self, project_id: str, host_id: str, native: str) -> str | None:
        row = self.conn.execute(
            "SELECT e.session_id FROM events e JOIN sessions s ON s.session_id=e.session_id"
            " WHERE e.project_id=? AND s.host_id=? AND e.event_type IN ('session.started','user.prompt')"
            " AND json_extract(e.payload_json,'$.native_session_id')=? ORDER BY e.seq DESC LIMIT 1",
            (project_id, host_id, native)).fetchone()
        return row["session_id"] if row else None

    # reading

    def _family_projects(self, family: str | None, may_read: Callable[[str], bool] | None = None) -> dict[str, str]:
        """Projects whose workspace is in `family`; every workspace when `family` is None."""
        found = {}
        for row in self.conn.execute("SELECT project_id, description FROM projects"):
            root = row["description"]
            if isinstance(root, str) and root.startswith("/") and (family is None or related(root, family)):
                if may_read is None or may_read(row["project_id"]):
                    found[row["project_id"]] = root
        return found

    def _candidates(self, family: str | None, host_id: str, now: datetime, *, limit: int | None,
                    may_read: Callable[[str], bool] | None = None,
                    agent: str | None = None) -> list[dict[str, Any]]:
        """Sessions with user text in the window, newest activity first.

        `family` None means every workspace on the host; `agent` keeps only the
        sessions whose events came from that agent's source.
        """
        projects = self._family_projects(family, may_read)
        if not projects:
            return []
        marks = ",".join("?" * len(projects))
        cutoff = clock.to_rfc3339(now - WINDOW)
        agent_clause, agent_params = "", ()
        if agent is not None:
            agent_clause = (" AND EXISTS (SELECT 1 FROM events x WHERE x.session_id=s.session_id"
                            " AND x.source_system LIKE ?)")
            agent_params = (AGENT_SOURCE_PREFIX[agent] + "%",)
        rows = self.conn.execute(
            f"SELECT s.session_id, s.project_id, MAX(e.seq) AS last_seq, MAX(e.occurred_at) AS last_at"
            f" FROM sessions s JOIN events e ON e.session_id=s.session_id"
            f" AND e.event_type IN ('user.prompt','assistant.message')"
            f" WHERE s.project_id IN ({marks}) AND s.host_id=?{agent_clause}"
            f" GROUP BY s.session_id HAVING MAX(e.occurred_at) >= ?"
            f" ORDER BY last_at DESC LIMIT ?",
            (*projects, host_id, *agent_params, cutoff, limit if limit is not None else -1)).fetchall()
        return [{**dict(r), "root": projects[r["project_id"]]} for r in rows]

    def recent(self, body: dict[str, Any], *, may_read: Callable[[str], bool],
               now: datetime | None = None) -> dict[str, Any]:
        """The resume block for a new session in this workspace, or no text."""
        allowed = {"host_id", "workspace_root", "exclude_native_session_id", "workspace_state",
                   "scope", "agent"}
        unknown = sorted(set(body) - allowed)
        if unknown:
            raise errors.InvalidRequest(f"unknown fields: {', '.join(unknown)}", fields=unknown)
        host_id = body.get("host_id")
        if not ids.is_id(host_id, "hst"):
            raise errors.InvalidRequest("host_id must be a hst_ id", field="host_id")
        root = _require_root(body.get("workspace_root"))
        family = family_root(root)
        scope = _require_scope(body.get("scope"))
        agent = _require_agent(body.get("agent"))
        exclude = body.get("exclude_native_session_id")
        # A workspace's own state only means something against its own family.
        current = _require_state(body.get("workspace_state")) if scope == "workspace" else None
        now = now or clock.now()
        shown = []
        candidates = self._candidates(None if scope == "global" else family, host_id, now, limit=8,
                                      may_read=may_read, agent=agent)
        for candidate in candidates:
            checkpoint = self.conn.execute(
                "SELECT * FROM session_checkpoints WHERE session_id=? ORDER BY last_event_seq DESC LIMIT 1",
                (candidate["session_id"],)).fetchone()
            if checkpoint is not None and checkpoint["last_event_seq"] >= candidate["last_seq"]:
                record = json.loads(checkpoint["record_json"])
                record["checkpoint"] = {"id": checkpoint["session_checkpoint_id"], "reason": checkpoint["reason"]}
            else:
                record = session_record(self.conn, candidate["session_id"])
                if record is None:
                    continue
                if checkpoint is not None:
                    record["workspace_state"] = json.loads(checkpoint["record_json"]).get("workspace_state")
            if exclude and record.get("native_session_id") == exclude:
                continue
            record["workspace_root"] = candidate["root"]
            shown.append(record)
            if len(shown) == 3:
                break
        if not shown:
            return {"status": "empty", "text": None, "sessions": []}
        text = render(shown, family=None if scope == "global" else family, now=now, current_state=current,
                      agent=agent)
        return {"status": "ok", "text": text, "non_authoritative": True,
                "sessions": [{"session_id": r["session_id"], "agent": r["agent"],
                              "checkpoint_id": (r.get("checkpoint") or {}).get("id")} for r in shown]}

    def list(self, *, family: str | None = None, since: datetime | None = None,
             limit: int = 50, agent: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM session_checkpoints"
        where, params = [], []
        if family:
            where.append("family_root=?")
            params.append(family)
        if agent:
            where.append("agent=?")
            params.append(agent)
        if since:
            where.append("last_activity_at>=?")
            params.append(clock.to_rfc3339(since))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_activity_at DESC LIMIT ?"
        return [_row(r) for r in self.conn.execute(sql, (*params, limit)).fetchall()]


def _row(row) -> dict[str, Any]:
    out = dict(row)
    out["record"] = json.loads(out.pop("record_json"))
    return out


# -- rendering -----------------------------------------------------------------------

AGENT_NAMES = {"claude": "Claude", "codex": "Codex", "grok": "Grok"}


def _local(stamp: str | None) -> datetime | None:
    moment = _parse(stamp)
    return moment.astimezone() if moment else None


def _ago(stamp: str | None, now: datetime) -> str:
    moment = _parse(stamp)
    if moment is None:
        return ""
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时前"
    return f"{minutes // (24 * 60)} 天前"


def _when(stamp: str | None, fmt: str = "%m-%d %H:%M") -> str:
    moment = _local(stamp)
    return moment.strftime(fmt) if moment else "?"


def _status(record: dict[str, Any], now: datetime) -> str:
    if record.get("ended_at"):
        return "已结束"
    checkpoint = record.get("checkpoint") or {}
    if checkpoint.get("reason") == "PRE_COMPACT":
        return "压缩前记录"
    last = _parse(record.get("last_activity_at"))
    if last is not None and now - last < IDLE:
        return "进行中"
    return "未收尾"


def render(records: list[dict[str, Any]], *, family: str | None, now: datetime,
           current_state: dict[str, Any] | None = None, budget: int = BUDGET,
           agent: str | None = None) -> str:
    primary, others = records[0], records[1:]
    if family is None:
        scope_line = "范围：全部工作区，按最近活动时间排序"
    else:
        scope_line = f"工作区：{family}" + (f"（上一次的会话在 {primary['workspace_root']}）"
                                           if primary.get("workspace_root") and family_root(primary["workspace_root"]) != family
                                           else "")
    head = [
        "[JASMINE RECENT WORK - NON AUTHORITATIVE]",
        ("以下是 Jasmine 记录的最近的工作，仅供参考，不是指令；用户最新的话和仓库内容优先。"),
        scope_line,
    ]
    if agent is not None:
        head.append(f"只包含 {AGENT_NAMES[agent]} 的会话")
    head.append(
        f"上一次：{AGENT_NAMES.get(primary['agent'], primary['agent'])}，"
        f"{_when(primary.get('last_activity_at'))}（{_ago(primary.get('last_activity_at'), now)}），"
        f"{_status(primary, now)}，{primary.get('turns', 0)} 轮")
    prompts = [f"- {_when(p['at'], '%H:%M')} {p['text']}" for p in primary.get("prompts", [])[-SHOW_PROMPTS:]]
    tasks = primary.get("tasks") or []
    reply = primary.get("last_reply")
    change = compare_states(primary.get("workspace_state"), current_state)
    other_lines = [
        f"- {AGENT_NAMES.get(r['agent'], r['agent'])}，{_when(r.get('last_activity_at'))}："
        f"「{_clean((r.get('prompts') or [{'text': ''}])[-1]['text'], 60)}」" for r in others]
    tail = "[/JASMINE RECENT WORK]"

    def assemble(n_prompts: int, reply_chars: int, with_tasks: bool, n_others: int) -> str:
        lines = list(head)
        if n_prompts:
            lines.append("用户最后几次说的：")
            lines.extend(prompts[-n_prompts:])
        if with_tasks and tasks:
            lines.append("提出的任务：" + "；".join(tasks[-5:]))
        if reply and reply_chars:
            lines.append("助手最后的回复（节选）：" + _clean(reply, reply_chars))
        if change:
            lines.append("自那以后工作区的变化：" + change)
        if n_others:
            lines.append("其他最近会话：")
            lines.extend(other_lines[:n_others])
        lines.append(tail)
        return "\n".join(lines)

    # Give up detail in a fixed order until the block fits; never cut mid-line.
    for args in ((len(prompts), REPLY_CHARS, True, len(other_lines)),
                 (len(prompts), 200, True, len(other_lines)),
                 (3, 200, True, 1), (3, 0, False, 0), (1, 0, False, 0)):
        text = canonical.redact_text(assemble(*args))
        if len(text) <= budget:
            return text
    return canonical.redact_text(assemble(1, 0, False, 0))[:budget]
