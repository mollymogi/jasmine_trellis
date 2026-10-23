# ADR 0029: Daily Conversation → Interpreter → Resolver → Memory

Status: implemented. Live acceptance and deployment evidence are recorded separately.

The native capture adapters collect immutable Raw Events, not Memory. A durable
local processing worker now feeds newly captured human `user.prompt` messages to
the existing P2 Interpreter, then the existing Resolver policy. It accepts the
Codex, Claude and Grok native producers on the configured local host. Assistant
messages remain `AGENT_PROPOSED` even when their capture actor is a system actor;
they cannot become `SYSTEM_CONFIG` or verified outcome evidence. An assistant
message can be explicitly selected by its Event ID, but is never auto-processed.

The daily prompt classifies standing project conventions/stable facts as PROJECT
RULE candidates and lasting adoption decisions as PROJECT DECISION candidates.
It returns existing P2 structures, not a new unrestricted Memory schema. Ordinary
chat can produce NO_STRUCTURE. Existing Resolver policy determines automatic task
application versus Review. Project/global conventions require the actual human
review command before creating Authority. Confidence never grants authority.
The browser shortcut only offers explicit human project RULE/DECISION candidates as NORMAL/CONTEXT rules.
High-impact candidates show an additional visible warning before that explicit
review confirmation; path/tool/global/implicit or agent-generated cases use the
existing Review detail page.

Actual approved Rule change events feed existing P4 SourceRegistry and the Memory
worker. Existing five Memory types and eligibility contracts remain unchanged.
Task-only application is shown separately from a long-term memory source. Raw
assistant assertions such as “tests passed” do not create Evidence or outcomes.
Memory remains historical, non-authoritative material; current Authority wins.
This async processing does not establish synchronous pre-tool native enforcement
or prove complete P6/P7/V1 acceptance or universal native Memory injection.

Schema 14 adds a source-keyed projection queue and append-only processing audit;
no Truth table is changed. Stable Interpreter/Resolver request keys and existing
source application uniqueness prevent duplicate application. Model network work
runs outside SQLite write transactions. Claims use expiring leases. Reserved
Interpretations survive crashes; failed/interrupted work is retried through the
existing audited child-Interpretation maintenance operation. Review completion
(including source replay) and actual Memory job/item state are read back rather
than inferred. Heartbeat staleness is visible in the UI.

Activation records the current Event sequence and processes only future messages.
Historical import is an explicit session/project action (at most 200 unprocessed
user messages per call); selecting an Event can process that exact source. The API
accepts no text, prompt, command, executable, provider, or model from the browser.
No hooks, trust records, capture credentials, or existing transcripts are changed.

The operator supplies the validated exact Codex executable and a frozen no-tools
catalog. The model runs with the existing data-only, ephemeral, isolated provider
profile, hooks/skills/apps/tools disabled, bounded output and exact schema/span
validation. The added CLI 0.163.0 fingerprint passed a real native stream extraction
preflight with gpt-6.1-sol. This consumes the operator's configured model access;
it is not a free local language model. Unavailable/changed binaries fail visibly.

The browser extension is opt-in (`P6_ENABLE_CONVERSATION_PROCESSING`). Development
and production proxies append only the three processing routes and enforce the
same-origin write boundary. No admin key route is exposed or secret injected.
Existing browser `interpretations:manage` queues source IDs; the registered system
processor runs the model and Resolver. Reviews retain their independent human and
Authority scopes.

Deployment uses fixed release entrypoints with the existing dependency Python.
Prepare only after schema migration and an explicit operator/user opt-in:

```sh
<python> -I -B <release>/scripts/deployment/prepare-conversation-processing.py \
  --base <install> --executable <validated-codex> --catalog <frozen-catalog> \
  --confirm-user-scope
```

Run `local-service.py --base <install> processing` as a durable service. Keep the
existing Core, Memory worker, dashboard and Hindsight settings; restart the first
three for the new release, without restarting Hindsight. First rehearse schema
13→14 and restore on a consistent SQLite backup. Before upgrading production,
stop database writers and take a fresh verified backup. Recovery stops all new
writers, preserves the current database separately, restores the matching schema
13 backup plus old runtime configuration, then starts the old services. There is
no destructive downgrade migration; events arriving after the backup require
separate reconciliation and must not be silently discarded.
