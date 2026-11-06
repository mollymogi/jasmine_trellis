"""Own one user-level OpenCode plugin file; preserve every other plugin (ADR 0040).

OpenCode loads `*.js` files from `~/.config/opencode/plugins/`. The installer
writes `jasmine-trellis.js` with the pinned interpreter, launcher, device binding
and receipts baked in, refuses to overwrite a file it does not own, keeps a
backup, and removes only its own file on `--disable`. The Memory hint is the
device binding's opt-in, read by the capture module on each prompt.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

from ..hosthook.binding import load_device, read_key
from ..hosthook.install import InstallError, _backup
from . import memory_hint
from .opencode_session import default_data_home

LABEL = "Jasmine daily OpenCode conversation capture"
NAME = "jasmine-trellis.js"
MARKER = "// JASMINE_TRELLIS_OPENCODE_PLUGIN=1"

PLUGIN = r"""// @LABEL@
// Written by jasmine_core.capture.install_opencode; reinstalling replaces this file.
@MARKER@
import { spawn } from "node:child_process";

const CONFIG = @CONFIG@;
const created = new Set();   // main sessions created while this OpenCode runs
const children = new Set();  // subagent sessions: never captured
const commands = new Set();  // a slash command's template is not the user's own words
const replies = new Map();   // session -> last finished assistant message id
const sent = new Map();      // session -> last reply id handed to capture
let chain = Promise.resolve();
let counter = 0;
const ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

const iso = (ms) => new Date(typeof ms === "number" ? ms : Date.now()).toISOString();

// One capture process at a time, in event order. A failure or timeout means no
// record and no context; OpenCode itself is never blocked or changed.
function capture(body, timeoutMs) {
  const job = chain.then(() => new Promise((resolve) => {
    let out = "";
    let done = false;
    const finish = (value) => { if (!done) { done = true; clearTimeout(timer); resolve(value); } };
    let child;
    try {
      child = spawn(CONFIG.python, ["-I", "-B", CONFIG.launcher, "--device-binding", CONFIG.device_binding,
        "--receipts", CONFIG.receipts, "--opencode-data", CONFIG.data_home],
        { stdio: ["pipe", "pipe", "ignore"], env: { PATH: "/usr/bin:/bin", HOME: process.env.HOME ?? "" } });
    } catch { resolve(null); return; }
    const timer = setTimeout(() => { child.kill("SIGKILL"); finish(null); }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      out += chunk;
      if (out.length > 262144) { child.kill("SIGKILL"); finish(null); }
    });
    child.on("error", () => finish(null));
    child.on("close", () => { try { finish(JSON.parse(out).context ?? null); } catch { finish(null); } });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(body));
  }));
  chain = job.catch(() => {});
  return job;
}

// An id OpenCode accepts for a part, sorting after the message's own parts.
function partID() {
  counter = (counter + 1) % 4096;
  const stamp = (BigInt(Date.now()) * 4096n + BigInt(counter)).toString(16).padStart(12, "0").slice(-12);
  let tail = "";
  for (let i = 0; i < 14; i++) tail += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  return "prt_" + stamp + tail;
}

export const JasmineTrellisPlugin = async () => ({
  event: async ({ event }) => {
    const properties = event?.properties ?? {};
    const info = properties.info;
    if (event?.type === "session.created" && info?.id) {
      if (info.parentID) { children.add(info.id); return; }
      created.add(info.id);
      await capture({ event: "session.created", sessionID: info.id, timestamp: iso(info.time?.created) }, 8000);
    } else if (event?.type === "message.updated" && info?.role === "assistant") {
      if (!children.has(info.sessionID) && info.time?.completed && !info.summary) replies.set(info.sessionID, info.id);
    } else if (event?.type === "session.idle") {
      const id = properties.sessionID;
      const reply = replies.get(id);
      if (!id || children.has(id) || !reply || sent.get(id) === reply) return;
      sent.set(id, reply);
      await capture({ event: "session.idle", sessionID: id, messageID: reply, timestamp: iso() }, 8000);
    }
  },
  "command.execute.before": async (input) => { if (input?.sessionID) commands.add(input.sessionID); },
  "chat.message": async (input, output) => {
    const id = input?.sessionID;
    const message = output?.message;
    if (!id || children.has(id) || message?.role !== "user") return;
    if (commands.delete(id)) return;
    const parts = Array.isArray(output.parts) ? output.parts : [];
    const text = parts.filter((p) => p?.type === "text" && !p.synthetic && !p.ignored && typeof p.text === "string")
      .map((p) => p.text).join("\n");
    if (!text.trim()) return;
    const context = await capture({ event: "chat.message", sessionID: id, messageID: message.id, text,
      timestamp: iso(message.time?.created), starting: created.delete(id) }, 4000);
    if (typeof context === "string" && context) {
      parts.push({ id: partID(), messageID: message.id, sessionID: id, type: "text", text: context, synthetic: true });
    }
  },
  "experimental.session.compacting": async (input) => {
    if (input?.sessionID && !children.has(input.sessionID)) {
      await capture({ event: "session.compacting", sessionID: input.sessionID, timestamp: iso() }, 4000);
    }
  },
});

export default { id: "jasmine-trellis.opencode", server: JasmineTrellisPlugin, setup() {} };
"""


def render(*, python: str, launcher: Path, binding: Path, receipts: Path, data_home: Path) -> str:
    config = {"python": str(Path(python).absolute()), "launcher": str(launcher.resolve(strict=True)),
              "device_binding": str(binding.resolve(strict=True)), "receipts": str(receipts.absolute()),
              "data_home": str(data_home.absolute())}
    return (PLUGIN.replace("@LABEL@", LABEL).replace("@MARKER@", MARKER)
            .replace("@CONFIG@", json.dumps(config, ensure_ascii=False)))


def install(home: Path, binding: Path, receipts: Path, *, python: str, launcher: Path,
            data_home: Path | None = None, confirmed: bool = False, disable: bool = False) -> dict:
    """`home` is OpenCode's config folder (`~/.config/opencode`)."""
    if not confirmed:
        raise InstallError("user-wide OpenCode capture requires explicit opt-in")
    if home.is_symlink() or (home / "plugins").is_symlink():
        raise InstallError("symlinked OpenCode configuration refused")
    device = load_device(binding)
    read_key(device.api_key_file)
    if device.human_api_key_file is None:
        raise InstallError("separate prompt credential required")
    read_key(device.human_api_key_file)
    target = home / "plugins" / NAME
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target.parent / ".jasmine-trellis.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink():
            raise InstallError("symlinked plugin file refused")
        original = target.read_bytes() if target.exists() else None
        if original is not None and MARKER.encode() not in original:
            raise InstallError("foreign plugin file will not be overwritten")
        if disable:
            memory_hint.set_enabled(binding, False)  # the hint never outlives capture
            backup = _backup(target, original) if original else None
            if original:
                target.unlink()
            return {"status": "disabled", "backup": str(backup) if backup else None}
        encoded = render(python=python, launcher=launcher, binding=binding, receipts=receipts,
                         data_home=data_home or default_data_home()).encode()
        if original == encoded:
            return {"status": "unchanged"}
        backup = _backup(target, original)
        temp = target.with_name("." + NAME + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
        os.replace(temp, target)
        return {"status": "installed", "backup": str(backup) if backup else None, "plugin": str(target),
                "events": ["session.created", "chat.message", "session.idle", "session.compacting"],
                "trust": "OpenCode loads user plugins on its next start; no other configuration is edited"}


def set_memory_hint(home: Path, binding: Path, *, enabled: bool, confirmed: bool = False) -> dict:
    """ADR 0036/0039: the plugin is unchanged; the opt-in lives in the device binding."""
    if not confirmed:
        raise InstallError("Memory hint requires explicit opt-in")
    target = home / "plugins" / NAME
    if enabled and (target.is_symlink() or not target.exists() or MARKER.encode() not in target.read_bytes()):
        raise InstallError("enable conversation capture before the Memory hint")
    if not memory_hint.set_enabled(binding, enabled):
        return {"status": "unchanged"}
    return {"status": "memory_hint_enabled" if enabled else "memory_hint_disabled"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("device-binding", "receipts", "launcher"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--opencode-home", type=Path, default=Path.home() / ".config" / "opencode",
                   help="OpenCode's config folder")
    p.add_argument("--opencode-data", type=Path, default=None, help="OpenCode's data folder (holds opencode.db)")
    p.add_argument("--confirm-user-scope", action="store_true")
    p.add_argument("--disable", action="store_true")
    p.add_argument("--memory-hint", choices=["enable", "disable"])
    a = p.parse_args()
    os.umask(0o077)
    if a.memory_hint:
        print(json.dumps(set_memory_hint(a.opencode_home, a.device_binding, enabled=a.memory_hint == "enable",
                                         confirmed=a.confirm_user_scope)))
        return
    print(json.dumps(install(a.opencode_home, a.device_binding, a.receipts, python=sys.executable, launcher=a.launcher,
                             data_home=a.opencode_data, confirmed=a.confirm_user_scope, disable=a.disable)))


if __name__ == "__main__":
    main()
