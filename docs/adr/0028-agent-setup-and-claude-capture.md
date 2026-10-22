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
message. A Hook name or prompt UUID alone is insufficient human provenance. For a
fresh user prompt, compare session ID, canonical cwd and exact text with a bounded
1 MiB tail of the client's native typed-input history, requiring a recent entry
and assigning it to only one native prompt UUID. It is evidence for this current
Hook, not a source of historical backfill. Ignore unmatched prompts and their
Stop replies. A private, locked per-session record retains the consumed identity,
text hash and a pending start. The main transcript path must lie directly inside
Claude's projects directory and match the native session UUID. Reject subagent
fields/paths, symlinks, foreign ownership and writable-by-others identity files.
The native transcript folder can be absent at startup; the adapter does not
create or change client session directories. No transcript body is scraped.

First verified typed input flushes a pending SessionStart, registers the canonical
workspace through existing Core APIs and records user.prompt with the dedicated
prompt credential. Stop reads only the native last_assistant_message and requires
a previously verified user turn. Prompt-history timestamps are labeled as such;
they are not presented as a native assistant-response timestamp. Session resumption
uses the same Core session. Failures never block or add context to Claude. Native
history suppression or very large pasted inputs without a matching literal entry
remain unverified; they are not silently attributed to a human. API error replies
are not invented as successful assistant messages.

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
