# ADR 0030: Separate human dashboard presentation from machine records

Status: implemented; browser verification is recorded separately.

The Interpretations page is a human-facing **对话提炼** list. Its default columns
are ordinal position, queue time, recorded Agent, source project/message role,
content summary, Chinese processing status and a detail action. The list number
is a view position, not a stable replacement ID. Time comes from the existing
queue record, with the full local date/time available on hover. Agent identity
comes from the recorded producer, never the model used for extraction. Unknown
sources remain unknown; unnamed projects do not fall back to opaque IDs.

Opening a normal detail shows original text, extracted meaning and processing
outcome, with applicable review/retry/Memory actions. IDs, model metadata and
source/audit links live under an explicitly closed technical disclosure. The
original interpretation collection remains available as lazy-loaded technical
records, preserving its filters, cursor semantics and traceability without making
those fields the daily view. The repeated capability/version banner is removed
from the interpretation list/detail routes. Navigation and recognized shared
status badges use Chinese labels. Core JSON, enums, request payloads, scopes,
route IDs and machine `data-status` values remain unchanged. Unknown tokens stay
visibly unknown, not mapped onto a success state.

EXTRACTED means **已提取**, never **已入库**. The daily processing list continues
to require an actual ACTIVE/RETAINED Memory item before displaying 已入库. Failed,
pending, task-only, no-action and obsolete-memory outcomes retain their separate
meanings. Review still requires an explicit action and a fresh server preview;
this presentation change does not approve anything automatically.

A known ambient browser wrapper is omitted only from the short display preview.
Original text and source spans remain unchanged and accessible. Credential masking
and React text escaping remain in effect. Desktop uses a compact table; mobile
reflows the same records into labeled rows without horizontal overflow. Technical
source links preserve exact original IDs. The HTML remains `Cache-Control:
no-store`; an already open old bundle requires one page refresh to receive the new
layout. No token persistence or client-side storage is introduced to work around
that refresh boundary.

No backend, schema, capture adapter, Gate, Memory eligibility or Hindsight change
is included. Only the dashboard is restarted during deployment. Existing mogi-ui
Button/Badge/Dialog primitives and the shared bundled colored Agent marks are
reused; this is application presentation, not a new UI-library API.
