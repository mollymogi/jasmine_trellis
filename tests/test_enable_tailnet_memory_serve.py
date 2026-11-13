"""Dry-run and pre-checks for tailnet Serve, against a fake tailscale binary."""

from __future__ import annotations

import os
import platform
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deployment/enable-tailnet-memory-serve.sh"
SERVE = "tailscale serve --bg --https=443 http://127.0.0.1:18798"


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class EnableTailnetMemoryServe(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.root, True)
        self.database = self.root / "core.db"
        self.status = self.root / "status.txt"
        self.after = self.root / "after.txt"
        self.log = self.root / "tailscale.log"
        self.env_file = self.root / "core.env"
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        _write_exec(self.fake_bin / "tailscale", """#!/bin/sh
printf '%s\\n' "$*" >> "$TAILSCALE_LOG"
if [ "$1" = "serve" ] && [ "$2" = "status" ]; then
  calls=$(cat "$TAILSCALE_STATUS_CALLS" 2>/dev/null || printf '0')
  calls=$((calls + 1))
  printf '%s' "$calls" > "$TAILSCALE_STATUS_CALLS"
  cat "$TAILSCALE_STATUS"
  if [ "$calls" -gt 1 ]; then
    exit "${TAILSCALE_POST_STATUS_RC:-0}"
  fi
  exit 0
fi
if [ "$1" = "serve" ] && [ "$2" = "--bg" ]; then
  if [ "${TAILSCALE_BG_RC:-0}" != "0" ]; then
    echo "serve failed" >&2
    exit "${TAILSCALE_BG_RC}"
  fi
  cat "$TAILSCALE_AFTER" > "$TAILSCALE_STATUS"
  exit 0
fi
if [ "$1" = "serve" ] && [ "$2" = "off" ]; then
  printf '%s\\n' "No serve config" > "$TAILSCALE_STATUS"
  exit 0
fi
echo "unexpected tailscale args: $*" >&2
exit 99
""")
        _write_exec(self.fake_bin / "lsof", """#!/bin/sh
printf '%s\\n' "lsof $*" >> "$PS_LOG"
echo 4242
""")
        _write_exec(self.fake_bin / "ps", """#!/bin/sh
printf '%s\\n' "ps $*" >> "$PS_LOG"
cat "$PS_BODY"
""")
        _write_exec(self.root / "listener-up", "#!/bin/sh\nexit 0\n")
        _write_exec(self.root / "listener-down", "#!/bin/sh\nexit 1\n")
        self.ps_log = self.root / "ps.log"
        self.ps_body = self.root / "ps-body.txt"
        self.status_calls = self.root / "status-calls"
        self.ps_body.write_text("\n")
        self.env_file.write_text("PATH=/usr/bin:/bin\nJASMINE_TAILNET_CLIENTS=1\n")
        self.status.write_text("No serve config\n")
        self.after.write_text(
            "https://mercuryliumac-mini.tail07df76.ts.net (tailnet only)\n"
            "|-- / proxy http://127.0.0.1:18798\n"
        )
        self._clients([])

    def _clients(self, rows: list[tuple[str, str, str]]) -> None:
        conn = sqlite3.connect(self.database)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tailnet_clients (
                host_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        conn.execute("DELETE FROM tailnet_clients")
        conn.executemany(
            "INSERT INTO tailnet_clients (host_id, display_name, status, created_at, revoked_at)"
            " VALUES (?, ?, ?, '2026-11-12T00:00:00Z', NULL)",
            rows,
        )
        conn.commit()
        conn.close()

    def _run(self, *args: str, listener: str = "down", core_env: str | None = None,
             script_env_flag: str | None = None, discover_core: bool = False,
             extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("JASMINE_TAILNET_CLIENTS", None)
        env.pop("JASMINE_TAILNET_SERVE_DB", None)
        env["PATH"] = str(self.fake_bin) + os.pathsep + env.get("PATH", "")
        env["TAILSCALE_LOG"] = str(self.log)
        env["TAILSCALE_STATUS"] = str(self.status)
        env["TAILSCALE_AFTER"] = str(self.after)
        env["TAILSCALE_STATUS_CALLS"] = str(self.status_calls)
        env["PS_LOG"] = str(self.ps_log)
        env["PS_BODY"] = str(self.ps_body)
        env["JASMINE_SERVE_LISTENER_CHECK"] = str(self.root / f"listener-{listener}")
        if discover_core:
            env.pop("JASMINE_CORE_ENV_FILE", None)
        else:
            env["JASMINE_CORE_ENV_FILE"] = str(self.env_file if core_env is None else Path(core_env))
        if script_env_flag is not None:
            env["JASMINE_TAILNET_CLIENTS"] = script_env_flag
        if extra_env:
            env.update(extra_env)
        if self.log.exists():
            self.log.unlink()
        self.status_calls.write_text("0")
        if self.ps_log.exists():
            self.ps_log.unlink()
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True, text=True, env=env, check=False,
        )

    def _log(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def test_script_does_not_target_core_or_funnel(self):
        text = SCRIPT.read_text()
        self.assertIn("tailscale serve off", text)
        self.assertIn("Do not roll back a migrated Core binary.", text)
        self.assertIn(SERVE, text)
        self.assertNotIn("http://127.0.0.1:18787", text)
        self.assertNotIn("tailscale funnel", text)
        self.assertNotIn("--bg", text.replace(SERVE, ""))

    def test_dry_run_prints_checks_without_a_listener_or_apply(self):
        self._clients([("hst_linux", "liuxu", "active"), ("hst_old", "VM-0-9-ubuntu", "revoked")])
        result = self._run("--db", str(self.database))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("listener: 127.0.0.1:18798 not listening (not required for dry-run)", result.stdout)
        self.assertIn("core_env: JASMINE_TAILNET_CLIENTS set", result.stdout)
        self.assertIn("serve_status: no Funnel and no forbidden backend", result.stdout)
        self.assertIn("linux_client: liuxu (hst_linux)", result.stdout)
        self.assertIn("windows: not enrolled", result.stdout)
        self.assertIn("mode: dry-run", result.stdout)
        self.assertIn("dry-run: not applying", result.stdout)
        self.assertIn(f"would_run: {SERVE}", result.stdout)
        self.assertEqual(self._log(), "serve status\n")
        self.assertNotIn("--bg", self._log())
        self.assertNotIn("serve off", self._log())

    def test_explicit_dry_run_allows_an_existing_gateway_backend(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        self.status.write_text(
            "https://mercuryliumac-mini.tail07df76.ts.net (tailnet only)\n"
            "|-- / proxy http://127.0.0.1:18798\n"
        )
        result = self._run("--dry-run", "--db", str(self.database), listener="up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("listener: 127.0.0.1:18798 listening", result.stdout)
        self.assertIn("no Funnel and no forbidden backend", result.stdout)
        self.assertEqual(self._log(), "serve status\n")

    def test_dry_run_refuses_funnel_and_forbidden_backends(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        for body in (
            "https://example.ts.net (Funnel on)\n|-- / proxy http://127.0.0.1:18798\n",
            "https://example.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:18787\n",
            "proxy http://127.0.0.1:15180\n",
            "proxy http://127.0.0.1:18084\n",
        ):
            with self.subTest(body=body):
                self.status.write_text(body)
                result = self._run("--dry-run", "--db", str(self.database))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("--bg", self._log())
                self.assertTrue(
                    "Funnel" in result.stderr or "18787" in result.stderr
                    or ":15180" in result.stderr or ":18084" in result.stderr
                )

    def test_missing_core_flag_is_not_taken_from_this_process(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        self.env_file.write_text("HOME=/tmp\n")
        result = self._run("--dry-run", "--db", str(self.database), script_env_flag="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JASMINE_TAILNET_CLIENTS", result.stderr)
        self.assertNotIn("--bg", self._log())

    def test_active_windows_client_is_not_enrolled_by_this_apply(self):
        self._clients([
            ("hst_linux", "VM-0-9-ubuntu", "active"),
            ("hst_win", "WIN-DRUN38VBEEM", "active"),
        ])
        result = self._run("--dry-run", "--db", str(self.database))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not enroll Windows", result.stderr)
        self.assertNotIn("--bg", self._log())

    def test_revoked_or_windows_only_rows_do_not_count(self):
        self._clients([
            ("hst_linux", "liuxu", "revoked"),
            ("hst_win", "windows-laptop", "active"),
        ])
        result = self._run("--dry-run", "--db", str(self.database))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not enroll Windows", result.stderr)
        self._clients([("hst_linux", "liuxu", "revoked")])
        result = self._run("--dry-run", "--db", str(self.database))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no active Linux client", result.stderr)

    def test_apply_uses_the_fake_binary_and_checks_the_backend(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        result = self._run("--apply", "--db", str(self.database), listener="up")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("backend: 127.0.0.1:18798", result.stdout)
        self.assertIn(f"applied: {SERVE}", result.stdout)
        self.assertEqual(
            self._log(),
            "serve status\nserve --bg --https=443 http://127.0.0.1:18798\nserve status\n",
        )
        self.assertNotIn("serve off", self._log())

    def test_apply_turns_serve_off_when_the_backend_or_funnel_is_wrong(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        self.after.write_text("https://example.ts.net (Funnel on)\n|-- / proxy http://127.0.0.1:18798\n")
        result = self._run("--apply", "--db", str(self.database), listener="up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Funnel", result.stderr)
        self.assertIn("serve off", self._log())
        self.status.write_text("No serve config\n")
        self.after.write_text("|-- / proxy http://127.0.0.1:18787\n")
        result = self._run("--apply", "--db", str(self.database), listener="up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("serve off", self._log())
        self.assertIn("18787", result.stderr)

    def test_core_environ_is_read_from_the_listening_process(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        self.ps_body.write_text(
            "/usr/bin/python3 core-entrypoint.py serve JASMINE_TAILNET_CLIENTS=1\n"
        )
        result = self._run("--dry-run", "--db", str(self.database), discover_core=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core_env: JASMINE_TAILNET_CLIENTS set", result.stdout)
        ps_log = self.ps_log.read_text()
        self.assertIn("lsof -nP -iTCP:18787 -sTCP:LISTEN -t", ps_log)
        if platform.system() == "Darwin":
            self.assertIn("ps -wwE -p 4242 -o command=", ps_log)
            self.assertNotIn("-ax", ps_log)
        else:
            self.assertIn("ps eww -p 4242", ps_log)
        self.ps_body.write_text(
            "/usr/bin/python3 core-entrypoint.py serve JASMINE_TAILNET_CLIENTS\n"
        )
        result = self._run(
            "--dry-run", "--db", str(self.database), discover_core=True, script_env_flag="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not have JASMINE_TAILNET_CLIENTS", result.stderr)
        self.assertNotIn("--bg", self._log())

    def test_failed_apply_does_not_run_serve_off(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        result = self._run(
            "--apply", "--db", str(self.database), listener="up",
            extra_env={"TAILSCALE_BG_RC": "3"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("tailscale serve failed", result.stderr)
        self.assertNotIn("serve off", self._log())
        self.assertEqual(self._log().count("serve status"), 1)
        self.after.write_text("tailscale: status unavailable\n")
        result = self._run(
            "--apply", "--db", str(self.database), listener="up",
            extra_env={"TAILSCALE_POST_STATUS_RC": "1"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("tailscale serve status failed", result.stderr)
        self.assertNotIn("serve off", self._log())
        self.assertIn("serve --bg", self._log())

    def test_apply_does_not_serve_when_the_listener_is_down(self):
        self._clients([("hst_linux", "VM-0-9-ubuntu", "active")])
        result = self._run("--apply", "--db", str(self.database), listener="down")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not listening", result.stderr)
        self.assertNotIn("--bg", self._log())


if __name__ == "__main__":
    unittest.main()
