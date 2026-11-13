"""A dependency-free HTTP client for the Core API.

Used by the capture entry, the validation runner and the tests. `urllib` is
standard library, so the capture hook has nothing to install on a machine that
only ever needs to post one event.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from contextvars import ContextVar, Token
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
TIMEOUT_SECONDS = 5.0
#: Armed only for a tailnet hook. Loopback leaves this unset and keeps its own timeouts.
_HOOK_DEADLINE: ContextVar[float | None] = ContextVar("jasmine_hook_deadline", default=None)


def arm_hook_deadline(deadline: float) -> Token:
    return _HOOK_DEADLINE.set(deadline)


def reset_hook_deadline(token: Token) -> None:
    _HOOK_DEADLINE.reset(token)


def hook_deadline() -> float | None:
    return _HOOK_DEADLINE.get()


class ApiError(Exception):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
        code = ""
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            code = str(payload["error"].get("code", ""))
        super().__init__(f"HTTP {status} {code}".strip())

    @property
    def request_id(self) -> str | None:
        """The Core's request id, so a refusal can be joined to the audit log."""
        if isinstance(self.payload, dict) and isinstance(self.payload.get("error"), dict):
            value = self.payload["error"].get("request_id")
            return str(value) if value else None
        return None

    @property
    def code(self) -> str:
        if isinstance(self.payload, dict) and isinstance(self.payload.get("error"), dict):
            return str(self.payload["error"].get("code", ""))
        return ""


class CoreClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str | None = None,
                 timeout: float = TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def set_token(self, token: str) -> None:
        self._token = token

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        timeout = self.timeout
        deadline = _HOOK_DEADLINE.get()
        if deadline is not None:
            # One attempt. A dead route waits at most the time still on the hook clock.
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError("hook budget exhausted")
        if deadline is None:
            return self._exchange(req, timeout)

        # Socket timeouts apply to each blocking operation, not the whole exchange
        # (including DNS, headers and error bodies). Bound the caller's wait too.
        result: queue.Queue = queue.Queue(maxsize=1)

        def exchange() -> None:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("hook budget exhausted")
                result.put((True, self._exchange(req, remaining, deadline)))
            except BaseException as exc:
                result.put((False, exc))

        threading.Thread(target=exchange, daemon=True, name="jasmine-hook-http").start()
        try:
            ok, value = result.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("hook budget exhausted") from None
        if time.monotonic() >= deadline:
            raise TimeoutError("hook budget exhausted")
        if not ok:
            raise value
        return value

    @staticmethod
    def _read(response, deadline: float | None) -> bytes:
        if deadline is None:
            return response.read()
        chunks = []
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("hook budget exhausted")
            # read1 returns after one underlying read, so a trickling peer cannot
            # keep a detached worker alive indefinitely inside read().
            chunk = response.read1(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _exchange(self, req, timeout: float, deadline: float | None = None) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(self._read(response, deadline).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                raw = self._read(exc, deadline).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"raw": raw[:500]}
            raise ApiError(exc.code, payload) from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body)
