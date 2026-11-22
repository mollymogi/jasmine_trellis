"""The cloud model behind background memory work, with a priority chain.

Background memory jobs (supersession, merging, nightly digests) ask a model a
question and expect one JSON object back. They go through the local Magpie
gateway, trying models in the configured order:

* the order comes from ``JASMINE_MEMORY_BRAIN_CHAIN`` (comma separated; set by
  the service from runtime.json ``memory_brain_chain``), else ``DEFAULT_CHAIN``;
* a provider whose quota Magpie reports as nearly spent goes to the back;
* a model that refuses (rate limit, auth, quota, model gone), times out or
  answers something that is not JSON is benched for a while and the next one
  is asked;
* only when the gateway itself is unreachable, or every model failed, does the
  caller see ``BrainUnavailable`` — and then backs off.

Nothing here runs on the Mac's CPU beyond the HTTP calls.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_GATEWAY = "127.0.0.1:3425"
DEFAULT_CHAIN = (
    "codex/gpt-6.2-sol",
    "codex/gpt-6-luna",
    "zhipu/glm-5.3",
    "opencode-go/deepseek-v4.1-flash",
    "grok/grok-4.7",
)
TIMEOUT_SECONDS = 45
QUOTA_FLOOR = 5          # percent remaining at or below which a provider counts as spent
QUOTA_CACHE_SECONDS = 60
BENCH_REFUSED = 900      # 429 / 401 / 402 / 403
BENCH_GONE = 3600        # 400 / 404: model unknown or request shape rejected
BENCH_FLAKY = 120        # 5xx, timeout, unreadable answer


class BrainUnavailable(Exception):
    """No model in the chain could answer right now."""


@dataclass(frozen=True)
class Answer:
    model: str
    data: dict[str, Any]
    tried: tuple[str, ...]


_bench: dict[str, float] = {}
_quota: dict[str, Any] = {"at": -1e9, "low": frozenset()}


def gateway_base() -> str:
    """Magpie's gateway from ~/.config/magpie/gateway.json (loopback only), else 3425."""
    addr = DEFAULT_GATEWAY
    try:
        data = json.loads((Path.home() / ".config/magpie/gateway.json").read_text(encoding="utf-8"))
        host, _, port = str(data.get("addr") or "").rpartition(":")
        if host in ("127.0.0.1", "localhost", "[::1]") and port.isdigit() and 0 < int(port) < 65536:
            addr = f"{host}:{port}"
    except (OSError, ValueError, AttributeError):
        pass
    return f"http://{addr}"


def chain() -> tuple[str, ...]:
    raw = os.environ.get("JASMINE_MEMORY_BRAIN_CHAIN", "")
    models = tuple(m.strip() for m in raw.split(",") if m.strip())
    return models or DEFAULT_CHAIN


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def low_quota_providers(now: float | None = None) -> frozenset[str]:
    now = time.monotonic() if now is None else now
    if now - _quota["at"] < QUOTA_CACHE_SECONDS:
        return _quota["low"]
    low: set[str] = set()
    try:
        with _opener().open(gateway_base() + "/v1/magpie/quotas", timeout=2) as response:
            raw = json.loads(response.read(262144).decode("utf-8", errors="replace"))
        entries = raw.get("data", raw) if isinstance(raw, dict) else raw
        for entry in entries if isinstance(entries, list) else []:
            provider = entry.get("provider") if isinstance(entry, dict) else None
            if not isinstance(provider, str):
                continue
            windows = [w for w in entry.get("windows") or [] if isinstance(w, dict)
                       and not w.get("lane") and isinstance(w.get("remaining"), (int, float))]
            if entry.get("error") or any(w["remaining"] <= QUOTA_FLOOR for w in windows):
                low.add(provider)
    except Exception:  # no quota view: keep the configured order
        pass
    _quota["at"], _quota["low"] = now, frozenset(low)
    return _quota["low"]


def order(models: tuple[str, ...], *, now: float | None = None) -> list[str]:
    """Configured order, benched models dropped, spent providers moved last."""
    wall = time.time() if now is None else now
    ready = [m for m in models if _bench.get(m, 0) <= wall]
    low = low_quota_providers()
    return ([m for m in ready if m.split("/", 1)[0] not in low]
            + [m for m in ready if m.split("/", 1)[0] in low])


def _bench_for(model: str, seconds: int) -> None:
    _bench[model] = time.time() + seconds


def _parse(content: str) -> dict[str, Any]:
    start, end = content.find("{"), content.rfind("}")
    data = json.loads(content[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _post(model: str, system: str, user: str, timeout: float) -> dict[str, Any]:
    body = {
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    request = urllib.request.Request(
        gateway_base() + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with _opener().open(request, timeout=timeout) as response:
        answer = json.loads(response.read().decode("utf-8", errors="replace"))
    return _parse(answer["choices"][0]["message"]["content"])


def ask_json(system: str, user: str, *, models: tuple[str, ...] | None = None,
             timeout: float = TIMEOUT_SECONDS,
             validate: Callable[[dict[str, Any]], Any] | None = None,
             log: Callable[[str], None] | None = None) -> Answer:
    """Ask the first model in the chain that can answer; return its JSON object."""
    tried: list[str] = []
    for model in order(models or chain()):
        tried.append(model)
        try:
            data = _post(model, system, user, timeout)
            if validate is not None:
                validate(data)
            return Answer(model=model, data=data, tried=tuple(tried))
        except urllib.error.HTTPError as exc:
            _bench_for(model, BENCH_REFUSED if exc.code in (401, 402, 403, 429)
                       else BENCH_GONE if exc.code in (400, 404) else BENCH_FLAKY)
            why = f"http_{exc.code}"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                _bench_for(model, BENCH_FLAKY)
                why = "timeout"
            else:  # the gateway itself is down: no other model can help
                raise BrainUnavailable(f"gateway_unreachable: {exc.reason}") from exc
        except ConnectionError as exc:
            raise BrainUnavailable(f"gateway_unreachable: {type(exc).__name__}") from exc
        except (TimeoutError, socket.timeout):
            _bench_for(model, BENCH_FLAKY)
            why = "timeout"
        except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
            _bench_for(model, BENCH_FLAKY)
            why = f"unreadable: {type(exc).__name__}"
        if log:
            log(f"brain: {model} failed ({why}); trying the next model")
    raise BrainUnavailable("no model answered" + (f" (tried {', '.join(tried)})" if tried else
                                                  " (every model is benched)"))
