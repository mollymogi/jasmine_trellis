# ADR 0028: Visible per-Agent setup and Claude Code capture

Status: implemented; current native acceptance boundaries are recorded separately.

Detecting a client is not a completed integration. The Agent connection dialog
therefore offers an actual **one-click Hook setup / disable Hook** action for
Codex, Claude Code and Grok, backed by the same restricted local operation. It
explains installation, receipts and errors separately. Unsupported clients remain
explicitly unsupported. A desktop-only detection does not authorize or establish
CLI Hook support. Skill invocation is not required for automatic conversation
capture; the page explains that distinction rather than offering an unrelated
Skill installer.

The native Claude adapter uses the four main-session hooks in
`~/.claude/settings.json`: SessionStart, UserPromptSubmit, Stop and SessionEnd.
The installer owns handlers with its specific label and launcher, not the settings
file. It backs up changes and retains foreign hooks and all unrelated settings.
It refuses symlinked configuration and does not override `disableAllHooks` or
client-owned trust records. Fresh interactive folders still use Claude's normal
trust dialog. Client restrictions, including account restrictions, are not bypassed.

Claude's UserPromptSubmit can also describe a scheduler wake or a teammate's
message. A Hook name or UUID alone is insufficient human provenance. Read only a
bounded 1 MiB tail of this session's native transcript and find the **exact prompt
UUID**. Require user-type metadata with `origin.kind=human`, `turnOrigin=human`,
`userType=external`, matching session/cwd, no sidechain, meta-message or tool-result
assistant reference. Accept only the observed entrypoint/source pairs: `cli/typed`
and `claude-desktop/sdk`. Desktop Code sends human submissions over the SDK transport;
`sdk` alone is insufficient. The latter pair was verified on actual desktop Code
2.1.308 transcripts and matching rejected native Hook receipts on 2026-10-23.
The connection panel explicitly limits Claude coverage to CLI and local desktop
Code; ordinary Chat, Cowork and cloud sessions are outside this adapter.
Text still comes from the current Hook, never from the transcript.
Unknown origins/older formats are ignored. No native input history is consumed,
and no previous transcript messages are imported.

Native transcript persistence can lag Hook dispatch. UserPromptSubmit runs
asynchronously and retries the bounded metadata read for up to 1.5 seconds. This
cannot stall model processing. A private, locked per-session record retains the
verified turn identity/text hash and a pending start. The transcript path must lie
directly inside Claude's projects directory and match the native session UUID.
Reject subagent fields/paths, symlinks, foreign ownership and non-private native
transcripts. The session directory may be absent at startup; the adapter does not
create or change client-owned session directories.

First verified typed input flushes a pending SessionStart, registers the workspace
through existing Core APIs and records user.prompt with its dedicated credential.
Stop reads only the native last_assistant_message. If an exceptionally fast Stop
beats the async prompt commit, it is held privately until that prompt commits;
unmatched auto-wake replies never become assistant records. Native prompt metadata
timestamps are labeled as prompt timestamps, never as assistant-response times.
Resume retains the Core session. Failures never block or add context to Claude.
API errors are not invented as successful assistant messages.

A version-2 operator profile pins separate fixed installers, destinations and
limited credentials per client. Local routes are restricted to
`POST /_local/agents/{codex|claude|grok}/capture` with only enable/disable input.
The existing live-Core authorization, exact same-origin/loopback checks and
pinned operator-token grant remain required. Legacy Grok profiles still read.
Codex's existing command bytes are preserved when enabling so an unchanged
installation retains its client-owned trust. No Core schema or admin proxy is
expanded. Disable preserves stored conversation records.

Trusted local deployment preparation is reproducible:

```sh
<runtime-python> -I -B <release>/scripts/deployment/prepare-agent-capture.py \
  --base <local-install-directory> --confirm-user-scope
```

This prepares the private profile and limited credentials, but installs no hooks.
Hook installation is then the explicit page action. It reuses existing bindings,
backs up changed operator configuration and does not print secrets. It neither
restarts Core nor changes Hindsight.

All Agent surfaces share colored logo badges, using the existing pinned upstream
silhouettes and license. Colors are dashboard presentation choices; monochrome
brands receive a white mark on a colored background. They remain distinct and
legible in both themes without runtime CDN assets.

Native reference: [Claude Code Hooks](https://code.claude.com/docs/en/hooks).

## Amendment (2026-10-28): subdirectories of the starting directory

A Claude session that changes into a subdirectory (`cd sub/deeper`) is still the same workspace. The starting directory recorded at session start defines the workspace and project; a turn whose working directory is that directory or anywhere below it is captured into the same session and project. A directory outside the starting directory (including a sibling with the same name prefix) is still refused as `native_workspace_changed`. The Memory hint uses the same rule, so Claude turns made in a subdirectory get the starting project's memories.

Observed live on local-item6 before this change: 48 of 62 turns in one long video session ran in subdirectories and were refused, and the Memory hint was refused with them. Those turns were not captured at all, so they were neither recorded nor hinted.

## Amendment (2026-10-29): bounded session state

Capture's private per-session state kept every verified turn for the life of the session. One long Claude session on local-item6 reached 128 turns and 65,859 bytes, past the 64 KiB limit for private files; from 2026-10-28 02:56 UTC every capture of that session failed (`BindingError`, 201 times in about a day and a half) and its Memory hint was refused with it. Those turns were not recorded and cannot be recovered from the hook.

The state now keeps only the newest 64 verified turns and the newest 16 pending Stops; capture and the hint only need recent turns. The state is read with a 1 MiB limit (other private files keep 64 KiB), so a state written before this bound still loads and is trimmed on its next write. The privacy checks on the file are unchanged.
