# ADR 0026: Opt-in device-wide Codex conversations

Status: implemented; native acceptance is recorded separately from component checks.

The project-local capture in ADR 0024 required a fixed project ID and an explicit
directory boundary. It did not connect an ordinary new project to the daily
dashboard. User-wide Host Hook installation alone (ADR 0019) did not supply that
missing ownership.

## Decision

`jasmine_core.capture.codex_session --device-binding FILE --receipts FILE`
uses one operator-owned loopback Core destination, Host, and two private key
files. No project marker or special prompt is required. A normal persisted main
Codex session must pass the existing transcript-header origin check before any
Project or Session can be created. Subagents and ephemeral prediction forks do
not register anything. This local origin check is not remote human authentication.

The nearest `.git` directory or file identifies the workspace root, including
when launched in a subdirectory. Without a Git marker, the canonical launch
directory is the workspace. Symlink aliases converge; nested repositories and
Git worktrees have their own workspace identity. The Host and canonical path
form the stable project source identity. Core's existing atomic object API
assigns the project ID and makes concurrent/retried registrations idempotent.
The directory name is the display name and the absolute path is its description,
so equally named folders remain distinguishable. Renaming/moving a workspace
starts a new project; automatic equivalence by Git remote is deliberately absent.

Native session identity, project, and Host determine a Session. SessionStart,
UserPromptSubmit, Stop, and SessionEnd record only their actual lifecycle event
or message; resuming reuses the Session. All automatic sessions have no Task.
The user-prompt key has only `events:write`, and the system key only
`objects:write` and `events:write`. Neither approves Authority, confirms Evidence,
injects Context, nor writes to Hindsight. Raw conversation storage and eligible
committed Memory sources remain separate.

`jasmine_core.capture.install_codex` installs exactly those four handlers in
`$CODEX_HOME/hooks.json` (default `~/.codex/hooks.json`) after explicit user-wide
opt-in. It preserves foreign handlers and metadata, saves a private backup, and
is idempotent. Python isolated mode prevents a project working directory or
`PYTHONPATH` from replacing the installed capture package. It never edits hook
trust hashes. Enable the Codex hooks feature, then review and trust the four
**Jasmine daily conversation capture** definitions once with `/hooks`. Future
projects use the same user-level definitions without per-project setup.

Conversation's project and session lists drain all creation-ordered pages and
poll the next offset every three seconds. Switching project cancels the previous
reader; a failed refresh retains the last good rows and retries. Session links
show the newest sessions first and identify their project. Message polling
continues through the existing Event cursor.

## Boundaries

This opt-in records conversations from this user's native Codex runtime on this
device, including supported Desktop sessions using that configuration. It does
not install Codex, change other devices or custom CODEX_HOME directories, backfill
older chats, bypass hook trust, or reconnect the retired Memory V0.

Core must be available when a hook runs. Failed writes produce a failed private
receipt and a visible hook error; they are never reported as accepted. There is
currently no background delivery queue or guarantee of backfilling those failed
messages. Assistant messages appear at Stop, not as streaming chunks; tool output
is not mislabelled as chat. A changed session working directory whose transcript
header no longer matches fails the existing origin check.

## Verification

Component checks exercise automatic registration, concurrent starts, identical
folder names, root aliases/subdirectories, replay, excluded prediction sessions,
installer backups/preservation, paginated polling and stale-response rejection.
Native acceptance additionally requires ordinary Codex invocation with persisted
user trust, a plain `你好`, matching private hook receipts, Core rows, and actual
daily Dashboard rendering. A one-off trust bypass is not proof of daily readiness.
