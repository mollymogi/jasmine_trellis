"""Own one DeepSeek Harness plugin and link it into existing dsh profiles.

dsh loads bundles named in a profile's `package.json`. The installer writes
`~/.dsh/jasmine-trellis/` (the plugin), points each existing profile's
`node_modules/@local/jasmine-trellis` at that directory, and adds the bundle.
It does not create profiles, call pnpm, or edit any other plugin. Disable
removes only those links. The Memory hint stays in the device binding.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys

from ..hosthook.binding import load_device, read_key
from ..hosthook.install import InstallError, _backup
from . import memory_hint

LABEL = "Jasmine daily DeepSeek Harness conversation capture"
PACKAGE = "@local/jasmine-trellis"
PLUGIN_DIR = "jasmine-trellis"
MARKER = "// JASMINE_TRELLIS_DSH_PLUGIN=1"

PLUGIN = r"""// @LABEL@
// Written by jasmine_core.capture.install_dsh; reinstalling replaces this file.
@MARKER@
import { createRequire } from "node:module";
import { spawn } from "node:child_process";

const CONFIG = @CONFIG@;
const require = createRequire(import.meta.url);
let createUserMessage = null;
try { if (CONFIG.llm) createUserMessage = require(CONFIG.llm).createUserMessage; } catch { /* capture still runs */ }

const started = new Set();
const prompted = new Map();
let chain = Promise.resolve();

const iso = (ms) => new Date(typeof ms === "number" ? ms : Date.now()).toISOString();

function textOf(message) {
  const blocks = Array.isArray(message?.content) ? message.content : [];
  return blocks.filter((block) => block?.type === "text" && typeof block.text === "string").map((block) => block.text).join("\n");
}

function human(message) {
  const kind = message?.source?.kind;
  return kind === "user" || kind === "user-rpc";
}

function mainSession(session) {
  const header = session?.header;
  if (!header || header.origin === "subagent" || (header.delegationDepth || 0) > 0) return null;
  if (typeof header.id !== "string" || typeof header.cwd !== "string" || !header.cwd.startsWith("/")) return null;
  return header;
}

function capture(body, timeoutMs) {
  const job = chain.then(() => new Promise((resolve) => {
    let out = "";
    let done = false;
    const finish = (value) => { if (!done) { done = true; clearTimeout(timer); resolve(value); } };
    let child;
    try {
      child = spawn(CONFIG.python, ["-I", "-B", CONFIG.launcher, "--device-binding", CONFIG.device_binding,
        "--receipts", CONFIG.receipts, "--dsh-home", CONFIG.dsh_home],
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

export const name = "jasmine-trellis";
export const inject = [];

export function apply(ctx) {
  ctx.on("session/created", (session) => {
    const header = mainSession(session);
    if (!header) return;
    started.add(header.id);
    void capture({ event: "session.created", sessionID: header.id, cwd: header.cwd,
      timestamp: iso(header.createdAt), starting: true, origin: header.origin ?? null,
      delegationDepth: header.delegationDepth ?? 0 }, 8000);
  });
  ctx.on("session/event", (session, event) => {
    const header = mainSession(session);
    if (!header || !event) return;
    if (event.type === "assistant/message") {
      const message = event.data?.message;
      const text = textOf(message);
      if (!message?.id || !text.trim()) return;
      void capture({ event: "assistant.message", sessionID: header.id, cwd: header.cwd, messageID: message.id,
        text, timestamp: iso(event.time), origin: header.origin ?? null, delegationDepth: header.delegationDepth ?? 0 }, 8000);
    } else if (event.type === "compaction/start") {
      void capture({ event: "session.compacting", sessionID: header.id, cwd: header.cwd, timestamp: iso(event.time),
        origin: header.origin ?? null, delegationDepth: header.delegationDepth ?? 0 }, 4000);
    }
  });
  ctx.on("agent/pre-step", async (payload, next) => {
    const decision = await next();
    if (!decision || decision.kind !== "enter") return decision;
    const header = mainSession(payload?.agent?.session);
    if (!header) return decision;
    let extra = "";
    for (const message of (decision.messages ?? []).filter(human)) {
      const text = textOf(message);
      if (!message?.id || !text.trim()) continue;
      let known = prompted.get(message.id);
      if (!known) {
        known = { timestamp: iso() };
        prompted.set(message.id, known);
        known.context = await capture({ event: "user.prompt", sessionID: header.id, cwd: header.cwd,
          messageID: message.id, text, timestamp: known.timestamp, starting: started.delete(header.id),
          origin: header.origin ?? null, delegationDepth: header.delegationDepth ?? 0 }, 4000);
      }
      if (typeof known.context === "string" && known.context) extra += (extra ? "\n\n" : "") + known.context;
    }
    if (!extra || !createUserMessage) return decision;
    if ((decision.messages ?? []).some((message) => message?.source?.kind === "jasmine-trellis")) return decision;
    return { ...decision, messages: [...decision.messages, createUserMessage({
      content: [{ type: "text", text: extra }], source: { kind: "jasmine-trellis" } })] };
  });
}
"""

PATCH = """# Jasmine Trellis conversation capture. Owned by install_dsh.
- insert:
    - id: jasmine-trellis
      name: "@local/jasmine-trellis"
"""

MANIFEST = {
    "name": PACKAGE,
    "version": "0.1.0",
    "private": True,
    "type": "module",
    "description": LABEL,
    "exports": {".": "./index.js"},
    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
}


def llm_entry() -> str | None:
    """The installed dsh copy of `createUserMessage`, if this machine has one."""
    candidates = []
    binary = shutil.which("dsh")
    if binary:
        resolved = Path(binary).resolve()
        # .../dsh/lib/bin.js or a bin shim whose real path is inside the package.
        for parent in (resolved.parent, *resolved.parents):
            candidate = parent / "node_modules" / "@deepseek-ai" / "dsh-llm" / "lib" / "index.js"
            if parent.name == "dsh":
                candidates.append(candidate)
            if parent.name == "lib" and parent.parent.name == "dsh":
                candidates.append(parent.parent / "node_modules" / "@deepseek-ai" / "dsh-llm" / "lib" / "index.js")
    candidates.append(Path("/opt/homebrew/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-llm/lib/index.js"))
    candidates.append(Path("/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-llm/lib/index.js"))
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return str(path)
        if path.is_symlink() and path.resolve().is_file():
            return str(path.resolve())
    return None


def render(*, python: str, launcher: Path, binding: Path, receipts: Path, dsh_home: Path) -> str:
    config = {"python": str(Path(python).absolute()), "launcher": str(launcher.resolve(strict=True)),
              "device_binding": str(binding.resolve(strict=True)), "receipts": str(receipts.absolute()),
              "dsh_home": str(dsh_home.absolute()), "llm": llm_entry()}
    return (PLUGIN.replace("@LABEL@", LABEL).replace("@MARKER@", MARKER)
            .replace("@CONFIG@", json.dumps(config, ensure_ascii=False)))


def _profiles(home: Path) -> list[Path]:
    root = home / "profiles"
    if not root.is_dir() or root.is_symlink():
        return []
    found = []
    for child in sorted(root.iterdir()):
        manifest = child / "package.json"
        if (child.name.startswith(".") or child.name == "node_modules" or child.is_symlink()
                or not child.is_dir() or not manifest.is_file() or manifest.is_symlink()):
            continue
        found.append(child)
    return found


def _write_bytes(target: Path, encoded: bytes) -> None:
    if target.is_symlink():
        raise InstallError("symlinked plugin file refused")
    temp = target.with_name("." + target.name + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
    os.replace(temp, target)


def _link_profile(profile: Path, plugin: Path, *, disable: bool) -> None:
    manifest_path = profile / "package.json"
    document = json.loads(manifest_path.read_text())
    if not isinstance(document, dict):
        raise InstallError("dsh profile manifest is not an object")
    dependencies = document.setdefault("dependencies", {})
    dsh = document.setdefault("dsh", {})
    profile_meta = dsh.setdefault("profile", {})
    bundles = profile_meta.setdefault("bundles", [])
    if not isinstance(dependencies, dict) or not isinstance(bundles, list):
        raise InstallError("dsh profile manifest is not the expected shape")
    current = dependencies.get(PACKAGE)
    link = "link:" + str(plugin)
    if current not in (None, link):
        raise InstallError("another package already uses @local/jasmine-trellis")
    node_modules = profile / "node_modules" / "@local"
    link_path = node_modules / "jasmine-trellis"
    if disable:
        if PACKAGE in dependencies:
            del dependencies[PACKAGE]
        profile_meta["bundles"] = [name for name in bundles if name != PACKAGE]
        if link_path.is_symlink() or link_path.exists():
            if link_path.is_symlink() and link_path.resolve() != plugin.resolve():
                raise InstallError("profile link does not point at the Jasmine plugin")
            link_path.unlink()
    else:
        dependencies[PACKAGE] = link
        if PACKAGE not in bundles:
            bundles.append(PACKAGE)
        node_modules.mkdir(parents=True, exist_ok=True, mode=0o700)
        if link_path.is_symlink():
            if link_path.resolve() != plugin.resolve():
                raise InstallError("profile link does not point at the Jasmine plugin")
        elif link_path.exists():
            raise InstallError("profile plugin path is not a symlink")
        else:
            link_path.symlink_to(plugin)
    original = manifest_path.read_bytes()
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    if original != encoded:
        _backup(manifest_path, original)
        _write_bytes(manifest_path, encoded)


def install(home: Path, binding: Path, receipts: Path, *, python: str, launcher: Path,
            confirmed: bool = False, disable: bool = False) -> dict:
    """`home` is dsh's home (`~/.dsh`)."""
    if not confirmed:
        raise InstallError("user-wide dsh capture requires explicit opt-in")
    if home.is_symlink():
        raise InstallError("symlinked dsh home refused")
    device = load_device(binding)
    read_key(device.api_key_file)
    if device.human_api_key_file is None:
        raise InstallError("separate prompt credential required")
    read_key(device.human_api_key_file)
    plugin = home / PLUGIN_DIR
    plugin.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = plugin / "index.js"
    with (plugin / ".jasmine-trellis.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if index.is_symlink():
            raise InstallError("symlinked plugin file refused")
        original = index.read_bytes() if index.exists() else None
        if original is not None and MARKER.encode() not in original:
            raise InstallError("foreign plugin file will not be overwritten")
        if disable:
            memory_hint.set_enabled(binding, False)
            for profile in _profiles(home):
                _link_profile(profile, plugin, disable=True)
            backup = _backup(index, original) if original else None
            if original:
                index.unlink()
            return {"status": "disabled", "backup": str(backup) if backup else None}
        encoded = render(python=python, launcher=launcher, binding=binding, receipts=receipts, dsh_home=home).encode()
        _write_bytes(plugin / "package.json", (json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n").encode())
        _write_bytes(plugin / "cordis.patch.yml", PATCH.encode())
        changed = original != encoded
        if changed:
            _backup(index, original)
            _write_bytes(index, encoded)
        before = {profile: (profile / "package.json").read_bytes() for profile in _profiles(home)}
        for profile in _profiles(home):
            _link_profile(profile, plugin, disable=False)
        linked = any((profile / "package.json").read_bytes() != before[profile] for profile in before)
        if not changed and not linked:
            return {"status": "unchanged"}
        return {"status": "installed", "plugin": str(index),
                "profiles": [profile.name for profile in _profiles(home)],
                "events": ["session/created", "agent/pre-step", "assistant/message", "compaction/start"],
                "trust": "dsh loads the bundle the next time each profile starts"}


def set_memory_hint(home: Path, binding: Path, *, enabled: bool, confirmed: bool = False) -> dict:
    """ADR 0036/0039: the plugin is unchanged; the opt-in lives in the device binding."""
    if not confirmed:
        raise InstallError("Memory hint requires explicit opt-in")
    target = home / PLUGIN_DIR / "index.js"
    if enabled and (target.is_symlink() or not target.exists() or MARKER.encode() not in target.read_bytes()):
        raise InstallError("enable conversation capture before the Memory hint")
    if not memory_hint.set_enabled(binding, enabled):
        return {"status": "unchanged"}
    return {"status": "memory_hint_enabled" if enabled else "memory_hint_disabled"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("device-binding", "receipts", "launcher"):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--dsh-home", type=Path, default=Path.home() / ".dsh", help="dsh home directory")
    p.add_argument("--confirm-user-scope", action="store_true")
    p.add_argument("--disable", action="store_true")
    p.add_argument("--memory-hint", choices=["enable", "disable"])
    a = p.parse_args()
    os.umask(0o077)
    if a.memory_hint:
        print(json.dumps(set_memory_hint(a.dsh_home, a.device_binding, enabled=a.memory_hint == "enable",
                                         confirmed=a.confirm_user_scope)))
        return
    print(json.dumps(install(a.dsh_home, a.device_binding, a.receipts, python=sys.executable, launcher=a.launcher,
                             confirmed=a.confirm_user_scope, disable=a.disable)))


if __name__ == "__main__":
    main()
