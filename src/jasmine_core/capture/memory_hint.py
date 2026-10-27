"""Prompt-time Memory hint, hook side (ADR 0036 phase A).

`request` asks Core for the hint with the device's `memory:recall`-only key and
never raises: any failure means "no hint" and the prompt proceeds unchanged.
`set_enabled` flips the per-Agent opt-in in the device binding; it is the only
writer of `memory_hint` and is called by the installers, never by a hook.
"""
from __future__ import annotations

import fcntl
import time
from pathlib import Path
from typing import Any

from ..api.client import CoreClient
from ..hosthook.binding import Binding, _read_json, load_device, read_key
from ..hosthook.dialects._common import additional_context
from ..hosthook.install import InstallError, _write

BUDGET_SECONDS = 1.5


def request(binding: Binding, *, project_id: str, native_session_id: str, prompt: str,
            session_id: str | None = None, deadline: float | None = None,
            client_factory=CoreClient) -> tuple[str | None, dict[str, Any]]:
    """Return `(text, receipt)`. The receipt holds counts and ids, never text."""
    started = time.monotonic()
    deadline = deadline if deadline is not None else started + BUDGET_SECONDS
    receipt: dict[str, Any] = {"status": "disabled"}
    if not binding.memory_hint or binding.memory_key_file is None:
        return None, receipt
    remaining = deadline - started
    if remaining <= 0.05:
        return None, {"status": "skipped", "reason": "no_time_left"}
    try:
        client = client_factory(binding.core_url, read_key(binding.memory_key_file), timeout=remaining)
        body = {"project_id": project_id, "native_session_id": native_session_id, "prompt": prompt}
        if session_id is not None:
            body["session_id"] = session_id
        answer = client.post("/v1/memory/hint", body)
        text = answer.get("text")
        if not isinstance(text, str) or not text or answer.get("non_authoritative") is not True:
            text = None
        receipt = {"status": answer.get("status"), "reason": answer.get("reason"),
                   "items": len(answer.get("items") or []) if text else 0,
                   "trace_id": answer.get("trace_id")}
    except Exception as exc:  # fail open; no token, prompt or body in the receipt
        text, receipt = None, {"status": "failed", "error_type": type(exc).__name__}
    if text is not None and time.monotonic() > deadline:
        text, receipt = None, {**receipt, "status": "late"}
    receipt["latency_ms"] = int((time.monotonic() - started) * 1000)
    return text, receipt


def envelope(text: str) -> str:
    return additional_context("UserPromptSubmit", text)


def set_enabled(binding_path: Path, enabled: bool) -> bool:
    """Set `memory_hint` in the device binding. Returns whether it changed."""
    binding_path = binding_path.resolve(strict=True)
    with (binding_path.parent / (binding_path.name + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Read as `load_device` does; a binding it accepts must not block
        # disabling capture merely because it predates the hint.
        document = _read_json(binding_path, "device binding file")
        if enabled:
            key = document.get("memory_key_file")
            if not key:
                raise InstallError("no Memory hint credential; rerun prepare-agent-capture.py")
            read_key(Path(key))
        if bool(document.get("memory_hint", False)) == enabled:
            return False
        if enabled:
            document["memory_hint"] = True
        else:
            document.pop("memory_hint", None)
        _write(binding_path, document)
        binding_path.chmod(0o600)
        load_device(binding_path)  # what was written must still load
        return True
