"""Wall clock for one tailnet capture hook. Loopback capture does not arm it."""
from __future__ import annotations

import time
from contextvars import Token

from ..api.client import arm_hook_deadline, hook_deadline, reset_hook_deadline
from ..hosthook.binding import TAILNET_CORE_ORIGIN

USER_PROMPT_SECONDS = 4.0
LIFECYCLE_SECONDS = 3.0
HINT_FLOOR_SECONDS = 1.2
#: Hint floor, plus git status (0.5s) and one RTT (0.2s), before recent may start.
RECENT_FLOOR_SECONDS = 1.9

_PROMPT = frozenset({
    "UserPromptSubmit", "user_prompt_submit", "chat.message", "PreInvocation", "user.prompt",
})
_LIFECYCLE = frozenset({
    "SessionStart", "SessionEnd", "PreCompact",
    "session_start", "session_end", "pre_compact",
    "session.created", "session.compacting",
})


def seconds_for(event: str | None) -> float | None:
    if event in _PROMPT:
        return USER_PROMPT_SECONDS
    if event in _LIFECYCLE:
        return LIFECYCLE_SECONDS
    return None


def arm_tailnet(core_url: str, event: str | None) -> Token | None:
    """Start the wall clock once. A nested call leaves the outer deadline in place."""
    if core_url != TAILNET_CORE_ORIGIN or hook_deadline() is not None:
        return None
    seconds = seconds_for(event)
    if seconds is None:
        return None
    return arm_hook_deadline(time.monotonic() + seconds)


def disarm(token: Token | None) -> None:
    if token is not None:
        reset_hook_deadline(token)
