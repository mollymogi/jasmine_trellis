# Local Agent discovery and Conversation navigation

The Conversation page lists projects registered in Core. It is not a machine-wide project crawler. Recorded project-created event metadata determines source groups; client installation never creates a source group or a project.

The reader has one full-height Agent → project → session navigation tree. It includes clients detected on this device even when they have no recorded projects; these nodes say installed but awaiting capture, and expanding them explains the boundary. There is no separate source selector or session picker. A compact Agent/time switch also offers a newest-first session timeline, grouped by local calendar day (today/yesterday/the remaining last seven days) and older month. Future dates and invalid dates have explicit buckets. Time grouping uses the session start timestamp, not inferred activity. It displays project identity on every session link, supports name/path search before limiting results, and reveals 40 rows at a time. Switching grouping preserves the selected conversation and reader state; metadata previews remain session-scoped and concurrency-limited. Expand/collapse controls do not change the current reader. Project and session links preserve independent scopes, while polling preserves expansion and selection. Only expanded projects and the selected project request first-message previews, with at most four readers. Project paths remain available as tooltips, search terms, and advanced scope details. The page omits the global release-status banner. Source details, polling, and manual review links remain available.

The Conversation page discovers known clients automatically on load; the tree and “Agent 接入” dialog share that result. The dialog supports refreshing both views. The dashboard host checks executable files in its PATH, standard Homebrew/local/npm user bin locations, Grok/OpenCode user bin locations, and explicitly named macOS application directories. It does not execute clients, recurse through disks, read conversation files, install hooks, or register projects. An absent result means not found at those locations; it does not establish that the client is absent elsewhere. CLI and desktop installation indicators are independent. Codex, Claude Code and Grok have native conversation capture adapters; installation still requires explicit opt-in.

The dashboard-local `GET /_local/agents` route is separate from the frozen `/v1` Core contract. Both production hosting and Vite use the same implementation. It requires a loopback Host, refuses foreign browser origins/cross-site fetches, and validates the caller's Bearer credential against the configured Core's fixed `/v1/projects?limit=1` read before inspecting files. No credentials are supplied by the server, no caller-selected target/path is used, and no file paths or token are included in the response. Other methods are refused. Results use `Cache-Control: no-store`.

To capture another Agent, implement its native event adapter, preserve working directory/session/actor/source attribution, configure the appropriate least-privilege Core credentials, enable the client's hook/plugin under its own trust rules, and verify an ordinary real conversation through Core and the dashboard. A detected installation, project registration, or existing icon alone is insufficient proof of capture.

## Agent logos

Navigation, the connection dialog, timeline entries and assistant avatars share bundled monochrome brand marks from [Lobe Icons](https://github.com/lobehub/lobe-icons), pinned to revision `ab8cbcc6ce806b923c547628132957c8a8b61509`. `web/src/assets/agents/SOURCE.json` records upstream files and checksums; `LICENSE.lobe-icons` preserves its MIT license. Each silhouette is rendered as a white mark inside a distinct colored badge in both themes; navigation, timeline, dialog and chat share the same colors. No runtime CDN or new package dependency is used. Unknown producers keep the neutral icon.

## Grok native capture

Grok Build 1.0.67 now has an opt-in native Hook adapter. The Agent connection dialog exposes enable/disable under an explicit operator profile; its read-only discovery and restricted mutation surfaces are separate. Configuration status, native message receipt and capture failure are distinct. See [ADR 0027](adr/0027-native-grok-conversation-capture.md) for ownership, authentication, provenance and real acceptance boundaries.


## One-click setup

The visible Agent connection action opens per-client setup controls for Codex,
Claude Code and Grok. “一键配置 Hook” installs only Jasmine's four handlers;
“停用 Hook” removes only those owned entries. Installed configuration and a native
message receipt remain separate states. Client installation alone
is never reported as successful capture. Unsupported clients are explicitly marked.
Skill invocation is not required for automatic conversations. See [ADR 0028](adr/0028-agent-setup-and-claude-capture.md)
for the private deployment preparation command, native input provenance and trust boundaries.
