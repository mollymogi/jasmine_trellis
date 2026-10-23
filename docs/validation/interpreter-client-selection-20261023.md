# Local Interpreter client selection — 2026-10-23

Scope: client-first settings/layout and the real Grok data-only adapter. Codex's
existing model/authentication qualification is retained. Claude and OpenCode are
explicitly deferred by the user, not represented as working extractors.

## Source/component checks

- Python full regression suite: passed (exit 0); then the changed provider/model
  checks passed again after the final Grok tool-inventory fix.
- Frontend full regression suite: passed; production TypeScript/Vite build passed.
  The existing bundle-size advisory remains.
- Focused checks cover client/model pairing, deferred clients, pinned binaries,
  native output/schema framing, unexpected tools, bounded child failure, local
  origin/operator authorization, revision conflicts and valid legacy backups.
- Browser component tests prove client → model selection, explicit validation
  without activation, save/readback and clear authentication errors.

## Native measurements

Grok 1.0.67, model `grok-4.6`, saved native login, real cloud inference:

- A preliminary `--tools ''` invocation still advertised 24 tools. That profile
  was rejected and was not deployed.
- The final explicit removal profile advertised **zero tools** and zero MCP
  servers. Its native stream was system/init → assistant → successful result.
  The output passed source/span/schema validation as `NO_STRUCTURE` for hello.
- This is a synthetic health measurement, not proof of a new human conversation,
  policy approval, persistent Memory write or general immunity to prompt injection.
- Claude authentication diagnosis was stopped at the user's request; OpenCode
  development/validation was also stopped. Neither is enabled by the registry.

## Browser and deployment

Staging Chrome passed client/model change, save/reopen and restoration to
`Codex / gpt-6.1-sol`, with only local settings writes. Both light/dark appearance
and a 390px viewport were inspected; no dialog horizontal overflow occurred.

Formal Chrome passed the live **验证可用性** call for Grok, selection/save/reopen,
and restoration to `Codex / gpt-6.1-sol`. The final release is
`4b169684cd5a3dfafca94139786d540f327d0c8c`; the preceding validated release has the
identical Git source tree. The final route readback passed after release restart.
All 62 prior Interpretation configuration hashes were preserved. Hindsight
remained running as PID 1590. Native capture Hooks, browser credentials, Agent
control and Memory-provider configuration hashes were unchanged. No schema or
network configuration change was performed.

A further **read-only** check used a genuine, previously captured human Claude
Code greeting as the immutable source. The runtime-configured Grok provider
returned schema/source-bound `NO_STRUCTURE`. This proves cross-Agent source
extraction and actual provider selection, while intentionally adding no business
record or Memory. Full persistent Conversation → review → Memory validation
remains documented in the earlier conversation-memory package.

Detailed outcomes are recorded in the accompanying manifest. Private evidence stays outside Git, including native
receipts, screenshots, deployment backups and configuration audits. No tokens,
conversation bodies or native authentication material are published.
