# Human-facing extraction dashboard — 2026-10-23

PASS for this presentation change. The formal Interpretations route now displays
**对话提炼** with only list position, queue time, recorded Agent, source project/
message role, content summary, Chinese state and a detail action. Opaque source,
interpretation/resolution IDs, provider version, confidence and model configuration
are absent from the default human view. The source Agent is independent of the
extraction model. Shared colored marks are reused.

Normal details show original text and extracted meaning. Machine identifiers and
source/audit links require opening Technical Details. The original technical
collection is fetched only after explicitly opening Technical Records. Its filters,
full identifiers, after_id cursor semantics and source links remain available.
Recognized status badges and business navigation use Chinese labels; original Core
enums remain unchanged in API/request bodies and data-status. EXTRACTED is 已提取,
not 已入库; retained, pending, failed, no-action, task-only and obsolete states keep
their separate meanings. Unknown tokens never acquire a successful state.

The existing P6 browser contract probe was adapted to explicitly open the technical
collection and read data-status rather than translated visible labels. Its syntax
was checked; this change does not claim a fresh complete P6 browser Gate run.

Validation: 642 frontend/server checks and production typecheck/build passed.
Focused checks cover human columns, actual producer identity versus the model,
lazy technical fetching, hidden technical disclosure, unknown status, source
preview preservation and the unchanged fresh-preview/explicit-approval contract.
Real Chrome used actual Core records through both a temporary local preview and
the formal dashboard; no fabricated sources or business writes were created.

Real production checks:

- Default innerText contains no displayed source/interpretation/resolution/review
  IDs and no provider/confidence/model/raw-status fields.
- Queue timestamps match the actual source rows; technical source links preserve
  exact original Event identities.
- Opening normal detail keeps technical IDs collapsed; technical records load
  only after the explicit disclosure action.
- Light/dark and 390-pixel mobile rendering have no horizontal overflow, including
  the normal detail dialog. Real screenshots remain private outside Git.
- No page errors and no non-GET Core requests occurred in browser verification.
- HTML is Cache-Control: no-store. An already running old JavaScript bundle needs
  one tab refresh; no token storage/persistence workaround was introduced.

Only the dashboard service restarted. Before switching the runtime release pointer,
all backend source, deployment scripts and proxy files were compared byte-for-byte
with the previous runtime and found unchanged. Core and Hindsight process IDs,
trusted Codex Hook bytes and browser credential bytes stayed unchanged. There was
no schema migration, capture reconfiguration, new approval or Hindsight write.

This is a human presentation change, not expanded P0-P7/V1 acceptance. Original
semantic-chain proof remains in [Conversation to Memory validation](conversation-memory-20261023.md).
See [ADR 0030](../adr/0030-human-dashboard-presentation.md) for presentation boundaries.
