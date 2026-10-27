# ADR 0033: A human Rule catalog with source-bound management

Status: implemented; runtime verification is recorded separately.

The previous Rules page exposed IDs, effective-scope queries and four audited
command forms simultaneously. With no global rules, it looked empty even when
project/task rules existed. The default page now browses actual stored rules
across projects, with content, named scope, use and Chinese approval state.
Search is explicitly over loaded rows. Source, version, matcher, evidence criteria
and audit identifiers remain available in details. Existing advanced command forms
and Hook-decision reads are retained behind explicit actions, not loaded as the
primary workflow. The old generic release-warning banner is omitted on this page.

## Additive read contract

`GET /v1/rules` requires `authority:read`. Optional single-valued filters are
`project_id`, `task_id`, `status`, `scope_kind`, `limit` (1..200, default 50), and
`offset` (nonnegative, default 0). Unknown/duplicate filters and invalid IDs,
statuses, bounds or project/task relationships are refused. Rows sort by stable
rule ID descending. `count` is this page's length; a limit+1 lookahead supplies
`has_more` and nullable `next_offset`. There is no claimed global total or
transactional snapshot across pages.

This is a **catalog ownership filter**, not an effective-rule calculation:
project filtering includes that project's project/task-owned rules; global rules
have their own filter. The unchanged `GET /v1/rules/active` still merges applicable
global/project/task ACTIVE rules for Guard/Context consumers. The collection
returns existing Rule objects, not project metadata through a broader permission.
Project/task names come from their existing separately-authorized object reads.
Both development and production proxies add this exact GET path; mutation/admin
allowlists, grants, Guard semantics and schema stay unchanged.

## Human operations

The guide distinguishes current Authority from historical Memory, proposal from
approval, and approval from actual Context delivery/native enforcement. No UI
status claims an Agent received a rule or that Guard is enabled. This increment
does not fix or enable the separately reviewed Guard paths.

Guided creation chooses a named project/task, an existing immutable source
record and readable content/use fields. Sources are filtered to the exact Rule
scope; changing scope clears the selection. Submit creates PROPOSED only. A
separate explicit approval is required. It does not fabricate a user message or
silently promote an Agent suggestion to Authority. The source picker can include
other record types where a user-prompt source is unavailable, preserving the
existing origin contract.

Approval/retirement send Core's read revision and a Registry-read host. Retirement
requires an explicit confirmation and preserves history. A guided edit locks the
original scope, retains matching/verification fields, requires a new selected
source and sends the current revision. Existing `event_id` replay identities,
conflict/unknown outcomes, permissions and actor checks are preserved; there are
no automatic retries or credential upgrades. In-flight dialogs cannot be dismissed.

Unsafe source/Rule text is rendered as React text and credential-shaped strings
are masked using the shared renderer. The UI never parses Rule text into executable
matchers. DENY/CONFIRM require explicit match conditions; tool/action/path fields
remain an AND, and path prefixes include descendants. Guard activation remains a
separate native integration operation.
