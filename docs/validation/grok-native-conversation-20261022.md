# Grok native conversation acceptance — 2026-10-22

Status: PASS for native conversation capture and local page controls.

The production dashboard at loopback port 15180 enabled capture using its real Agent connection button. Disable and re-enable also succeeded. The operator profile fixes the adapter and private credentials; no arbitrary command, target, key or project binding was supplied by the browser. The final state remains enabled.

Ordinary `grok` TUI invocations in an existing Git worktree and a separate plain directory each received only `你好`. A native `--resume` invocation in the first workspace received another `你好`. No wrapper, per-project Jasmine file, special acceptance prompt or direct synthetic Core seeding was used. Input was entered by the operator through PTY; this is not a separate manually typed human acceptance or remote human authentication.

| Native session | Core project | Core session | Prompts / replies |
|---|---|---|---|
| `01a162fd-33a2-7ac2-b435-27daa3cbbf6e` | `prj_01M5HFTD6GQHPHYZ1KSXT3SWS3` | `ses_01M5HFTD6SGB8N20XJX6VTZB7V` | 2 / 2 |
| `01a162fe-3dcc-77e3-a936-64f126999edf` | `prj_01M5HFWFNX82B7ADZGM1HMXKJX` | `ses_01M5HFWFP6581AATWDB8XKAV0H` | 1 / 1 |

The adapter produced 12 accepted lifecycle/message receipts (three each of SessionStart, UserPromptSubmit, Stop and SessionEnd), plus three ignored non-user/unsupported events and no failed receipts. Resumption retained the first project and Core session; the two workspaces have different project/session identities.

Independent verification compared the three prompt strings and three assistant strings with these fresh native sessions’ persisted messages, then checked receipt text hashes, native session/turn identity, source namespace, actor kinds and Core events. Actual Chrome rendered exactly the expected message IDs for each session and displayed the Grok assistant label/logo. The dialog changed from installed/waiting to received-message evidence. Agent and time views both located the native session. Browser page errors were empty; its Core traffic remained GET-only and its three configuration writes used only the restricted local capture endpoint.

The first two native turns encountered cloud connection retries before completing; no model, proxy, DNS or Grok configuration was changed to obtain a reply, and no reply was fabricated from an earlier session.

Validation: 28 Python capture/installer/Codex regression checks; 616 frontend/server checks; additional focused control/UI checks after the bounded private-file reader and refresh guard changes; production typecheck/build. Permission tests reject foreign-origin installation, unpinned reader credentials, revoked credentials, caller-selected command fields, unsafe profile permissions and symlinks. The adapter rejects synthetic prompt IDs, child/hidden sessions, native-identity substitutions and writable/symlinked summaries, while preserving whitespace and replay.

The existing Core and Hindsight process IDs were unchanged. Codex Hook bytes were unchanged; foreign Grok Hook files were preserved. The new Grok system key grants only objects:write/events:write; its prompt key grants only events:write. No schema migration, Gate enforcement integration, historical backfill, streaming-message or Memory-retention claim is made.

Private native receipts, owned Hook/config backups, message-hash manifest and browser screenshots remain outside Git. Hook source and installer are reviewable in ADR 0027. This result does not establish full P6/P7 or V1 acceptance.
