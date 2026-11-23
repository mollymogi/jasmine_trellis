"""Project cards: a short, model-written summary of each workspace's memory.

A card says what the project is, where it stands and the decisions and
conventions that hold, in a few lines. A new session in that workspace gets it
next to the recent-work block, so the agent starts from the project's state
instead of from whichever five notes recall happened to rank first.

Rules that keep the card honest:

* The card is written only from ACTIVE memory and rules of that workspace
  (split projects folded in), never from the model's own knowledge. Each line
  must name the numbered notes it rests on; a line that cites nothing, or cites
  a number that was not given, is dropped. The stored card keeps the memory ids.
* Faded and superseded observed notes are left out the same way recall leaves
  them out (``hint._evolved``).
* Container folders (the home folder, AgentApp) get no card: what is said there
  is about other projects.
* A card is rebuilt only when its source notes changed, at most once a day per
  project, and only in the night window unless asked for. One project per idle
  tick of the memory worker; when no model answers, it cools down.
* Cards live as JSON files next to core.db (``data/project_cards``), not in the
  schema: they are a derived view that can be deleted and rebuilt at any time.
* A card older than ``MAX_AGE_DAYS`` is not shown.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from . import brain, hint
from .fs_view import _canonical_groups, _items, _rules
from .scope import container_projects, established_projects, workspace_roots

MIN_ITEMS = 5               # fewer notes than this: nothing worth summarising
MAX_SOURCES = 60            # newest notes handed to the model (rules always go in)
MAX_LINES = 7
LINE_CHARS = 90
SUMMARY_CHARS = 120
REFRESH_SECONDS = 20 * 3600
MAX_AGE_DAYS = 14
NIGHT_HOURS = range(2, 7)   # local hours the worker may refresh in on its own
COOLDOWN_SECONDS = 1800
TIMEOUT_SECONDS = 90
RENDER_BUDGET = 900

PROMPT = """你在为一个软件/创作项目写「项目卡」：给新开的 AI 编码会话一眼看懂这个项目的现状。
输入是该项目在记忆库里的规则和笔记，每条有编号 n。只能根据这些条目写，不要补充条目里没有的事实。

输出一个 JSON 对象：
{"summary": "一句话：这个项目是什么、现在在做什么（不超过 60 字）",
 "lines": [{"text": "一条要点（不超过 45 字）", "sources": [条目编号, ...]}, ...]}

要求：
- lines 3 到 7 条，按对新会话的重要性排序：仍然有效的决定和约定、当前进展或卡点、踩过的坑。
- 每条 lines 必须在 sources 里列出它依据的条目编号，至少一个。
- 新旧条目冲突时以时间更新的为准，不要把已被推翻的旧说法写进去。
- 一次性的临时操作（某次改某张图、某个按钮）不要写。
- 用中文，简洁，不要客套话。"""


def cards_dir_for(conn: sqlite3.Connection) -> Path | None:
    """`data/project_cards` beside the main database file, or None for an in-memory database."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2]).resolve().parent / "project_cards"
    return None


def _now_iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))


def _card_path(folder: Path, project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_]+", project_id):
        raise ValueError("bad project id")
    return folder / f"{project_id}.json"


def read_card(folder: Path | None, project_id: str) -> dict[str, Any] | None:
    if folder is None:
        return None
    try:
        return json.loads(_card_path(folder, project_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def card_for_project(folder: Path | None, project_id: str) -> dict[str, Any] | None:
    """The card whose workspace group holds `project_id` (canonical or a split member)."""
    card = read_card(folder, project_id)
    if card is not None or folder is None:
        return card
    try:
        paths = sorted(folder.glob("*.json"))
    except OSError:
        return None
    for path in paths:
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if project_id in (card.get("members") or []):
            return card
    return None


def _write_card(folder: Path, card: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = _card_path(folder, card["project_id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# -- sources -------------------------------------------------------------------------

def sources(conn: sqlite3.Connection, ids: tuple[str, ...], *, now: float) -> tuple[list[str], list[dict]]:
    """(rules, notes) a card for this workspace group is written from, notes newest first."""
    marks = ",".join("?" * len(ids))
    items = _items(conn, f"project_id IN ({marks}) AND (task_id IS NULL OR task_id = '')", ids)
    evolved = hint._evolved(items, now)
    kept = [i for i in items if i["memory_id"] not in evolved]
    strong = [i for i in kept if i["pinned"] or i["admission"] == "AUTHORITY"]
    observed = [i for i in kept if i not in strong]
    notes = (strong + observed)[:MAX_SOURCES]
    return [r[2:] for r in _rules(conn, ids)], notes


def fingerprint(rules: list[str], notes: list[dict]) -> str:
    digest = hashlib.sha256()
    for rule in rules:
        digest.update(b"r" + rule.encode("utf-8"))
    for note in sorted(notes, key=lambda n: n["memory_id"]):
        digest.update(b"n" + note["memory_id"].encode("ascii"))
    return digest.hexdigest()[:20]


def _payload(name: str, rules: list[str], notes: list[dict]) -> tuple[str, dict[int, str]]:
    numbered: dict[int, str] = {}
    lines = [f"项目：{name}", "", "规则（用户确认过，始终有效）："]
    n = 0
    for rule in rules:
        n += 1
        numbered[n] = "rule:" + hashlib.sha256(rule.encode("utf-8")).hexdigest()[:12]
        lines.append(f"[{n}] {rule}")
    lines += ["", "笔记（新的在前）："]
    for note in notes:
        n += 1
        numbered[n] = note["memory_id"]
        label = hint.TYPE_LABELS.get(note["memory_type"], note["memory_type"])
        lines.append(f"[{n}] ({note['created_at'][:10]} {label}) {note['sanitized_text'].strip()[:300]}")
    return "\n".join(lines), numbered


def _clip(text: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def checked_lines(data: dict[str, Any], numbered: dict[int, str]) -> tuple[str, list[dict]]:
    """The summary and the lines that cite at least one given note; others dropped."""
    lines = []
    for entry in data.get("lines") or []:
        if not isinstance(entry, dict):
            continue
        cited = []
        for ref in entry.get("sources") or []:
            try:
                key = int(ref)
            except (TypeError, ValueError):
                continue
            if key in numbered and numbered[key] not in cited:
                cited.append(numbered[key])
        text = _clip(entry.get("text"), LINE_CHARS)
        if cited and text:
            lines.append({"text": text, "sources": cited})
        if len(lines) == MAX_LINES:
            break
    return _clip(data.get("summary"), SUMMARY_CHARS), lines


def _validate(numbered: dict[int, str]) -> Callable[[dict[str, Any]], None]:
    def check(data: dict[str, Any]) -> None:
        summary, lines = checked_lines(data, numbered)
        if not summary or len(lines) < 2:
            raise ValueError("card without a summary or with fewer than two cited lines")
    return check


def write_card(conn: sqlite3.Connection, folder: Path, project_id: str, name: str,
               ids: tuple[str, ...], *, now: float, ask: Callable[..., Any] = brain.ask_json,
               log: Callable[[str], None] | None = None, force: bool = False) -> dict[str, Any]:
    rules, notes = sources(conn, ids, now=now)
    if len(notes) < MIN_ITEMS:
        return {"project_id": project_id, "status": "too_few", "notes": len(notes)}
    mark = fingerprint(rules, notes)
    old = read_card(folder, project_id)
    if not force and old is not None and old.get("fingerprint") == mark:
        return {"project_id": project_id, "status": "unchanged"}
    text, numbered = _payload(name, rules, notes)
    answer = ask(PROMPT, text, timeout=TIMEOUT_SECONDS, validate=_validate(numbered), log=log)
    summary, lines = checked_lines(answer.data, numbered)
    card = {"project_id": project_id, "name": name, "members": list(ids),
            "generated_at": _now_iso(now), "generated_ts": now, "model": answer.model,
            "fingerprint": mark, "source_count": len(notes) + len(rules),
            "summary": summary, "lines": lines}
    _write_card(folder, card)
    return {"project_id": project_id, "status": "written", "model": answer.model, "lines": len(lines)}


def card_groups(conn: sqlite3.Connection) -> list[tuple[str, str, tuple[str, ...]]]:
    """Workspace groups that may get a card: not container folders."""
    containers = container_projects(workspace_roots(conn), established_projects(conn))
    return [(pid, name, ids) for pid, name, ids in _canonical_groups(conn) if pid not in containers]


class CardRefresher:
    """Idle-time refresh for the memory worker: one project per tick, night only."""

    def __init__(self, conn: sqlite3.Connection, folder: Path, *,
                 ask: Callable[..., Any] = brain.ask_json,
                 log: Callable[[str], None] | None = None,
                 now: Callable[[], float] = time.time,
                 hours: range | None = NIGHT_HOURS) -> None:
        self._conn, self._folder, self._ask, self._log, self._now = conn, folder, ask, log, now
        self._hours = hours
        self._cool_until = 0.0
        self._checked: dict[str, float] = {}

    def tick(self) -> dict[str, Any]:
        now = self._now()
        if now < self._cool_until:
            return {"status": "cooling"}
        if self._hours is not None and time.localtime(now).tm_hour not in self._hours:
            return {"status": "outside_window"}
        for pid, name, ids in card_groups(self._conn):
            if now - self._checked.get(pid, 0) < REFRESH_SECONDS:
                continue
            card = read_card(self._folder, pid)
            if card is not None and now - float(card.get("generated_ts") or 0) < REFRESH_SECONDS:
                self._checked[pid] = float(card.get("generated_ts") or now)
                continue
            self._checked[pid] = now
            try:
                result = write_card(self._conn, self._folder, pid, name, ids, now=now,
                                    ask=self._ask, log=self._log)
            except brain.BrainUnavailable as exc:
                self._cool_until = now + COOLDOWN_SECONDS
                self._checked.pop(pid, None)
                if self._log:
                    self._log(f"project card: {exc}; cooling down")
                return {"status": "cooling"}
            if self._log and result["status"] == "written":
                self._log(f"project card: {name} rewritten by {result['model']}")
            return {**result, "status": "done", "card": result["status"]}
        return {"status": "idle"}


def refresh_all(conn: sqlite3.Connection, folder: Path, *, project: str | None = None,
                force: bool = False, ask: Callable[..., Any] = brain.ask_json,
                log: Callable[[str], None] | None = None) -> list[dict[str, Any]]:
    """Rebuild cards now (CLI). Changed sources only unless `force`."""
    out = []
    now = time.time()
    for pid, name, ids in card_groups(conn):
        if project and project not in ids:
            continue
        try:
            out.append(write_card(conn, folder, pid, name, ids, now=now, ask=ask, log=log, force=force))
        except brain.BrainUnavailable as exc:
            out.append({"project_id": pid, "status": "unavailable", "error": str(exc)})
            break
    return out


def render(card: dict[str, Any] | None, *, now: float | None = None,
           budget: int = RENDER_BUDGET) -> str | None:
    """The session-start block for a card, or None when there is none or it is too old."""
    if not card or not card.get("lines"):
        return None
    now = time.time() if now is None else now
    age = now - float(card.get("generated_ts") or 0)
    if age > MAX_AGE_DAYS * 86400:
        return None
    day = time.strftime("%m-%d", time.localtime(float(card["generated_ts"])))
    head = ["[JASMINE PROJECT CARD - NON AUTHORITATIVE]",
            f"项目：{card.get('name', '')}（{day} 由记忆自动汇总，{card.get('source_count', 0)} 条来源；"
            "与用户最新的话或仓库内容冲突时以后者为准）",
            card.get("summary") or ""]
    tail = "[/JASMINE PROJECT CARD]"
    lines = [f"- {line['text']}" for line in card["lines"]]
    while lines:
        text = "\n".join([*head, *lines, tail])
        if len(text) <= budget:
            return text
        lines.pop()
    return None
