# Interpreter model selection — 2026-10-23

PASS: bounded model-control implementation, local preview and a real alternate-model
call. Formal deployment/readback is pending and must be recorded separately.

The installed Codex CLI reported successful ChatGPT login, and its saved auth mode
was inspected without exposing credential values. The actual operator model cache
contains seven visible candidates: gpt-6.1-sol, gpt-6-astra, gpt-6-sol, gpt-6-luna,
gpt-5.6-sol, gpt-5.6-terra and gpt-5.6-luna. This establishes a current catalog, not
universal model entitlement or successful cloud calls for all seven candidates.

Validation completed before daily deployment:

- Python: 1,876 tests, 11 skipped, no failures. Tests include per-invocation frozen
  choice, next-operation reload, unknown/unsafe model rejection, private-file and
  symlink rejection, and idempotent operator preparation over a >64 KiB catalog.
- Frontend/server: 649 checks passed. Origin, live authorization, pinned operator,
  CAS/stale changes, body restrictions, catalog integrity, no secret/path readback,
  read-only viewers, save failures and the actual dashboard management route are covered.
- Real Chrome preview: seven-model selector, save/reopen/reload persistence,
  light/dark/mobile rendering and no Core business writes.
- After selecting gpt-6-luna through that preview, a fresh provider invocation read
  the selection and actually called the saved-login Codex CLI. It processed an
  already captured genuine desktop user greeting, returned NO_STRUCTURE and passed
  source-bound output validation. This check created no Core business event,
  Rule, Review or Memory. It is a real model call, not native Hook acceptance or
  a new P0-P7/full-V1 Gate claim. Existing gpt-6.1-sol processing has prior real proof.

See [ADR 0031](../adr/0031-interpreter-model-selection.md) for security/credential,
model availability and existing-result boundaries. Private screenshots, model output
and configuration/CLI proof remain outside Git. This feature changes neither the
network configuration nor the prior security findings about external inference.
