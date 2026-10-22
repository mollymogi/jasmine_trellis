# ADR 0027: Native Grok conversations and local capture controls

Status: implementation and component checks complete; native production acceptance recorded separately.

Grok Build 1.0.67 emits native SessionStart, UserPromptSubmit, Stop and SessionEnd
hooks. A single owned `~/.grok/hooks/jasmine-conversation.json` integrates these
with the existing Core object and raw-event APIs. It applies to arbitrary Git
and non-Git workspaces; no wrapper, project-local marker, special prompt, broad
folder trust change, or history backfill is required. Global Hook loading follows
Grok's documented native rules, without editing any trust store.

The adapter verifies session UUID/canonical cwd against the owner-controlled
Grok session `summary.json`. It reads identity metadata only, never historical
message bodies. Grok's summary is 0644 inside an owner-only session directory;
requiring that native file to be 0600 would reject valid sessions. Reject symlinks,
foreign ownership, writable-by-others files, hidden/subagent sessions, mismatched
transcript paths, and malformed/contradictory native identities. SessionStart can
precede persistence: stage it privately and publish it before the first verified
prompt instead of inventing a persisted origin. Raw native timestamps are retained.
Mutable summary fields do not enter event identities, preserving replay.

Grok's current PromptOrigin contract identifies normal user prompts with UUID
prompt IDs. Synthetic wakes, parent messages, goal nudges, scheduling and plan
resumes use non-UUID IDs; they do not become human user.prompt records. This is a
local runtime provenance boundary, not remote human authentication. Only the
explicit native prompt/assistant text is recorded; no model text is inferred from
tool output. Native Stop text can be clipped by Grok at 32768 characters; this
adapter does not claim a full transcript or streaming response.

Separate private system and human-prompt credentials grant only objects:write +
events:write and events:write respectively. Source names start with
`grok-native-conversation-v1`, so producer identity is separate from Codex. Canonical
workspace roots use the same Git-marker policy as ADR 0026. Automatic sessions
have no Task; the adapter does not approve Rules, confirm Evidence, inject Context
or directly retain Memory. Core failures produce failed private receipts and
native Hook diagnostics; failure never blocks the user's prompt or continues a
model turn. There is no durable delivery queue or historical recovery claim.

## Page controls

The Agent connection dialog exposes Enable capture and Disable capture for Grok.
A private operator profile fixes the installer, Python, adapter launcher, Grok home,
Core binding and receipt paths. The browser supplies only enable/disable. The
standalone and dev hosts share `POST /_local/agents/grok/capture`; no new frozen
Core endpoint, schema version or admin proxy surface is introduced.

The profile explicitly grants this local operation to a SHA-256-pinned operator
browser credential. An objects:read grant alone is insufficient. Validate that
credential against live Core, require exact same-origin POST and loopback Host,
reject foreign origins, extra body fields and excessive request bodies, run only
the configured argv without a shell, and coalesce repeated clicks. Store private
operation receipts; do not return credentials, paths, arbitrary installer output
or prompt text. Preserve all foreign Hook files, back up owned changes, and refuse
symlink/foreign-name conflicts. Disable removes only the owned file and preserves
stored conversations. Source and user Hook configuration remain inspectable.

The UI distinguishes supported adapter, installed configuration, native message
receipt and recent capture failure. Shared discovery refreshes while the dialog
is open. "Enabled" is configuration proof, not end-to-end acceptance; a native
message receipt is still checked against Core and actual dashboard rendering.

## Native acceptance

Use the formal local profile, enable from the real dashboard, start ordinary
`grok` in a fresh workspace and send `你好`, then match native session/turn IDs,
message hashes, private receipts, Core events/project ownership and rendered
Conversation message IDs. Repeat in a second workspace without a new binding;
resumption must retain the original Core session. Synthetic/subagent rejection,
replay, configuration conflicts and permissions also have component checks.
