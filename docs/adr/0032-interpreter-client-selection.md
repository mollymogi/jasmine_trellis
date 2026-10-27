# ADR 0032: Select a local Interpreter client before its model

Status: implemented; native/deployment evidence is recorded separately.

The human control is **提炼器设置 → 提炼客户端 → 模型**. Codex and Grok
are enabled in this increment. Claude and OpenCode are detected as installed but
remain disabled, following the operator's request to defer them. Detection is
not an account entitlement or a successful inference claim. The list header
keeps the title/count on the left and settings/refresh together on the right.

The existing private control profile upgrades to version 2. It pins client IDs,
absolute executables, exact SHA-256 values and model candidates discovered through
the installed client. Browser input is limited to `{provider, model,
expected_revision}`. Version 1 selections remain readable. Writes retain a valid
private backup and audit, use compare-and-swap revisions and require same-origin,
live Core read authorization plus the pinned operator grant. Unsupported,
deferred or cross-client model choices fail closed. Model catalog preparation
preserves a version 2 profile/selection rather than silently downgrading it.

Each operation snapshots its selected provider/model/config. The actual provider,
model, binary fingerprint and prompt digest are stored in the existing immutable
Interpretation config. Historical results and already-started calls are unchanged.
The frozen API's legacy `codex-local-v1` extractor ID remains the logical entry
point; actual inference provenance is the stored provider/config, not that legacy
alias. There is no automatic switch to another client/model on failure.

Grok uses saved native authentication through a symlink in a private temporary
GROK_HOME, a temporary working directory and a dedicated leader socket. It does
not copy credentials into Core or accept browser-provided endpoints/authentication.
Native compatibility hooks, MCP discovery, workflows and project configuration
are absent/disabled in that private context. All qualified built-in tools and MCP
meta-tools are explicitly removed. **An empty `--tools` argument alone is not
sufficient in Grok 1.0.67**. The native streaming init must report an empty tool
list, assistant blocks must contain only text/thinking, and a successful terminal
schema output is required. Tool-bearing or malformed transcripts are rejected.
The supported Grok binary is pinned in code; an upgrade needs requalification.

A macOS sandbox additionally denies subprocess execution except the pinned CLI
and native keychain helper, and denies file writes outside the invocation's
private temporary directory. It does not claim complete read/network isolation.
Requests use Grok's native cloud transport and existing host routing; they are not
OpenAI requests. Authentication refresh failures are reported, not worked around
by exporting tokens or modifying the native client's authentication/configuration.
Timeout/output limits and owned-process cleanup remain bounded.

**验证可用性** invokes a fixed deployment-owned helper with a labeled synthetic
hello. It validates the response against the same source/schema but never writes
Conversation, Rule, Review or Memory. It does not activate the tested selection.
Only one distinct local health check runs at a time. The browser cannot supply
commands, paths, prompts, URLs or credentials. The control stays behind the
existing daily-processing feature flag; there is no Core schema migration.

Trusted deployment preparation (no inference or native-client configuration edits):

```sh
<runtime-python> -I -B <release>/scripts/deployment/prepare-interpreter-clients.py \
  --base <local-install-directory> --confirm-local-clients
```

Primary implementation references: installed Grok 1.0.67
`docs/user-guide/14-headless-mode.md`, `26-config-reference.md`, native `--help`,
and its measured streaming tool inventory. ADR 0031 remains the predecessor for
Codex catalog/authentication qualification; this ADR extends its control payload.
