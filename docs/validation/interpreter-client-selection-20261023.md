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

Formal deployment and live browser outcomes are recorded in the accompanying
manifest after verification. Private evidence stays outside Git, including native
receipts, screenshots, deployment backups and configuration audits. No tokens,
conversation bodies or native authentication material are published.
