"""The read-only L0/L1/L2 Markdown view of Memory (mem v2 step 4)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from jasmine_core import db
from jasmine_core.memory import fs_view
from test_memory_hint import Build


class FsView(Build):
    def test_three_levels_with_evolution_applied_in_l1_only(self):
        items = self.seed("截图统一放到桌面的 shots 文件夹。", "截图统一放到桌面的 shots 文件夹里。",
                          "发布前先跑一遍完整测试。")
        other = next(i for i in items if "发布前" in i["sanitized_text"])
        old, new = sorted((i for i in items if i is not other), key=lambda i: (i["created_at"], i["memory_id"]))
        with db.transaction(self.conn):
            self.conn.execute("UPDATE memory_items SET pinned=1 WHERE memory_id=?", (other["memory_id"],))
        with tempfile.TemporaryDirectory() as tmp:
            report = fs_view.export(self.conn, tmp)
            index = (Path(tmp) / "INDEX.md").read_text(encoding="utf-8")
            (folder,) = (Path(tmp) / "projects").iterdir()
            readme = (folder / "README.md").read_text(encoding="utf-8")
            detail = (folder / "OBSERVED_DECISION.md").read_text(encoding="utf-8")
        self.assertEqual(report["files"], 3)
        self.assertIn(f"projects/{folder.name}/README.md", index)
        # L1: pinned under 已确认, the superseded near-duplicate left out.
        confirmed, recent = readme.split("## 最近观察", 1)
        self.assertIn("📌", confirmed)
        self.assertIn("发布前", confirmed)
        self.assertIn(new["sanitized_text"], recent)
        self.assertNotIn(old["sanitized_text"], recent)
        # L2: everything, traceable by memory_id.
        for item in (old, new, other):
            self.assertIn(item["memory_id"], detail)


if __name__ == "__main__":
    unittest.main()
