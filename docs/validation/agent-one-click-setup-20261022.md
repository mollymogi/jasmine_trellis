# Agent setup and colored logos — 2026-10-22

PASS: production page setup/reconfiguration, disable and re-enable; colored icons;
native Claude first-prompt capture. NOT VERIFIED: a successful live Claude reply,
because the external service did not permit generation. No assistant reply was
inferred or fabricated. Component checks cover the normal Stop path and a reply
arriving before the asynchronous prompt handler commits.

The formal dashboard exposes per-client configuration controls for Codex, Claude
Code and Grok. Seven real browser operations passed: unchanged Codex reconfigure,
and configure → disable → configure for Claude and Grok. Final configuration is
enabled for all three. Codex definitions remained byte-for-byte identical, retaining
the existing client-owned trust. Claude's unrelated user settings were preserved.
Read-only browser credentials and foreign-origin writes remain rejected.

A fresh ordinary Claude Code 2.1.296 TUI session in a previously unused plain folder
received only `你好`, entered by the operator through PTY. UserPromptSubmit ran via
the globally installed native Hook with no project-local settings or wrapper.
The exact prompt UUID and typed/human metadata matched the freshly persisted
native row; the current Hook text hash, Core event and actual rendered message ID
all matched. The first input flushed its pending SessionStart. There was no
successful assistant turn to verify. This is not a separate manually typed human
acceptance, a history backfill, or full V1/P6/P7 acceptance.

The actual in-app browser was refreshed and its Agent connection dialog opened:
three supported clients show reconfigure/disable controls; OpenCode explicitly
remains unsupported. Codex, Claude and Grok use distinct colored badges in the
tree, timeline, setup dialog and assistant avatars. Real Chrome rendered light and
dark screenshots, and a 390-pixel mobile view had no horizontal dialog overflow.
Core browser traffic remained GET-only; setup actions used the restricted local
endpoint. The current Core and Hindsight process IDs were unchanged.

Validation: 35 Python capture/installer/regression checks; 620 frontend/server
checks; production typecheck/build. Private native receipts, browser screenshots,
configuration backups and verification scripts remain outside Git. The accompanying
manifest includes source IDs and hashes without tokens or user workspace paths.
See ADR 0028 for installation and provenance boundaries.
