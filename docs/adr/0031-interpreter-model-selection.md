# ADR 0031: Local, bounded Interpreter model selection

Status: implemented; deployment/native validation is recorded separately.

The daily Interpreter previously fixed `gpt-6.1-sol` in code. The dashboard now
provides a small **模型设置** dialog on 对话提炼. It explains the use of saved local
Codex authentication, external model processing, new-analysis-only changes and
absence of automatic fallback. Technical configuration stays outside the human
list. This controls the Interpreter only, not native Agent defaults or Hindsight.

A trusted deployment command prepares a private catalog from the operator's local
Codex model cache. Only visible catalog models are candidates; account entitlement
is not inferred from a cache entry. The command strips optional execution tools,
requires the existing exact-binary provider qualification and validates each entry
against the same data-only execution profile. No new credentials are created.
The browser cannot refresh a catalog, supply a model URL, command, credential,
executable, prompt or tool profile. Selection is limited to the pinned catalog IDs.

`GET /_local/interpreter` requires live Core objects:read authorization and a local
origin. Readback contains model labels/IDs, selected model, revision and management
permission; it excludes paths, credentials, catalog instructions and operator hashes.
`POST /_local/interpreter` additionally requires exact same-origin and a pinned
operator token. Its entire body is `{model, expected_revision}`. Unsupported IDs
and extra fields are rejected; a stale revision returns 409. Changes keep private
backups and an operational audit. These local management routes are gated by the
same daily-processing flag in both development and production dashboard servers.
No Core schema, auth grant or frozen P2/P4 API is expanded.

The selected model is stored in an owner-only file. Each new Interpreter operation
reads and validates that file; an invocation already started keeps its frozen
configuration, prompt and catalog bytes. Only the chosen safe model is passed to
Codex in a per-invocation catalog; unknown/unsafe choices fail closed. The exact
selection is recorded in the existing immutable Interpretation config. Existing
results do not change or rerun. A failed call retains its actual failed state and
does not silently select another model. Invalid selection files do not fall back
to the default model. Legacy single-model catalogs retain their original bytes and
profile fingerprint behavior.

Authentication still belongs to Codex. The current installation was verified as
ChatGPT login, rather than API-key login; the selector does not read/export tokens
or promise a specific plan, model entitlement or remaining quota. No daily Codex/
Claude/Grok Hook, TUN/proxy setting, credential or Hindsight model is modified.

Trusted preparation (configuration only; no model request or service restart):

```sh
<runtime-python> -I -B <release>/scripts/deployment/prepare-interpreter-models.py \
  --base <local-install-directory> --confirm-model-management
```

Source references: [Codex authentication](https://learn.chatgpt.com/docs/auth),
[Codex app-server and ChatGPT sign-in](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server).
