"""Read-only Markdown view of Memory (mem v2 step 4, "jasmine_mem_fs").

Three levels, so an Agent or the user can open the smallest file that answers:

* L0 ``INDEX.md`` — active global Rules, global Authority items and one line
  per workspace with counts. Small enough to read whole.
* L1 ``projects/<slug>/README.md`` — the workspace's Rules, Authority items and
  the newest observed notes after query-time evolution (faded or superseded
  notes left out, same rules as the hint).
* L2 ``projects/<slug>/<memory_type>.md`` — every active item of that type,
  with its memory_id, so a line can be traced back to its source.

Split capture projects are shown under their canonical workspace (confirmed
workspace aliases, as recall reads them). The view is regenerated from core.db
and never written back: core.db stays the only source of truth.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import hint
from .scope import merged_project_ids

L1_OBSERVED = 30


def _slug(name: str, project_id: str) -> str:
    base = re.sub(r"[^\w.-]+", "-", name or "", flags=re.U).strip("-")[:40] or "project"
    return f"{base}-{project_id[-6:]}"


def _line(item: dict[str, Any], *, ids: bool = False) -> str:
    text = hint._SPACE.sub(" ", item["sanitized_text"] or "").strip()
    label = hint.TYPE_LABELS.get(item["memory_type"], item["memory_type"])
    pin = "📌 " if item["pinned"] else ""
    tail = f" `{item['memory_id']}`" if ids else ""
    return f"- {pin}{label} · {item['created_at'][:10]} — {text}{tail}"


def _rules(conn: sqlite3.Connection, project_ids: tuple[str, ...] | None) -> list[str]:
    where, params = ("r.scope_kind='global'", ()) if project_ids is None else (
        "r.project_id IN (%s) AND r.task_id IS NULL" % ",".join("?" * len(project_ids)), project_ids)
    rows = conn.execute(
        "SELECT v.content, v.kind FROM rules r JOIN rule_versions v"
        " ON v.rule_id=r.rule_id AND v.version=r.current_version"
        f" WHERE r.status='ACTIVE' AND {where} ORDER BY r.created_at", params).fetchall()
    return list(dict.fromkeys(f"- {r[0].strip()}" for r in rows))


def _items(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT memory_id, memory_type, admission, trust, pinned, created_at, sanitized_text"
        f" FROM memory_items WHERE status='ACTIVE' AND {where} ORDER BY created_at DESC", params)]


def _canonical_groups(conn: sqlite3.Connection) -> list[tuple[str, str, tuple[str, ...]]]:
    projects = {r[0]: r[1] for r in conn.execute(
        "SELECT project_id, name FROM projects WHERE status='active'")}
    with_memory = {r[0] for r in conn.execute(
        "SELECT DISTINCT project_id FROM memory_items WHERE status='ACTIVE' AND project_id IS NOT NULL")}
    folded: set[str] = set()
    groups = []
    canon = [r[0] for r in conn.execute(
        "SELECT DISTINCT project_id FROM workspace_aliases WHERE confirmed=1")]
    for pid in canon:
        members = merged_project_ids(conn, pid)
        folded.update(members)
        ids = tuple(p for p in (pid, *members) if p in with_memory)
        if ids and pid in projects:
            groups.append((pid, projects[pid], ids))
            folded.add(pid)
    for pid in sorted(with_memory - folded):
        if pid in projects:
            groups.append((pid, projects[pid], (pid,)))
    return groups


def export(conn: sqlite3.Connection, out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    (out / "projects").mkdir(parents=True, exist_ok=True)
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    head = f"<!-- generated from core.db at {stamp}; read-only view, edits are not read back -->\n"
    written = 0
    index = [head, "# Jasmine Memory\n", "## 全局规则\n", *(_rules(conn, None) or ["- （无）"]), "",
             "## 全局记忆\n"]
    index += [_line(i) for i in _items(conn, "scope_kind='global'", ())] or ["- （无）"]
    index += ["", "## 工作区\n", "| 工作区 | 规则 | 记忆 | 文件 |", "|---|---|---|---|"]
    for pid, name, ids in _canonical_groups(conn):
        marks = ",".join("?" * len(ids))
        items = _items(conn, f"project_id IN ({marks})", ids)
        rules = _rules(conn, ids)
        evolved = hint._evolved(items, now)
        strong = [i for i in items if i["pinned"] or i["admission"] == "AUTHORITY"]
        observed = [i for i in items if i not in strong and i["memory_id"] not in evolved]
        slug = _slug(name, pid)
        folder = out / "projects" / slug
        folder.mkdir(exist_ok=True)
        readme = [head, f"# {name}\n", f"`{pid}`" + (f"（合并 {len(ids) - 1} 个拆分项目）" if len(ids) > 1 else ""), "",
                  "## 规则\n", *(rules or ["- （无）"]), "", "## 已确认\n",
                  *([_line(i) for i in strong] or ["- （无）"]), "",
                  f"## 最近观察（{min(L1_OBSERVED, len(observed))}/{len(observed)}，已去重、已淡出 {len(evolved)} 条）\n",
                  *([_line(i) for i in observed[:L1_OBSERVED]] or ["- （无）"]), "",
                  "## 明细\n"]
        by_type: dict[str, list[dict[str, Any]]] = {}
        for i in items:
            by_type.setdefault(i["memory_type"], []).append(i)
        for mtype, rows in sorted(by_type.items()):
            (folder / f"{mtype}.md").write_text(
                head + f"# {name} · {hint.TYPE_LABELS.get(mtype, mtype)}（{len(rows)}）\n\n"
                + "\n".join(_line(i, ids=True) for i in rows) + "\n", encoding="utf-8")
            readme.append(f"- [{hint.TYPE_LABELS.get(mtype, mtype)}]({mtype}.md)（{len(rows)}）")
            written += 1
        (folder / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
        written += 1
        index.append(f"| [{name}](projects/{slug}/README.md) | {len(rules)} | {len(items)} | {len(by_type)} |")
    (out / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return {"out_dir": str(out), "files": written + 1}
