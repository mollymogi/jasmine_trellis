# Interpreter model selection — 2026-10-23

PASS: bounded model control, preview/formal browser persistence, and real model calls
for gpt-6-luna and the restored gpt-6.1-sol default. Other catalog candidates were
validated against the bounded local profile, without claiming their cloud entitlement.

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


Formal deployment/readback:

- Runtime source: `242160a2b61e6e54b5410a724bd3a2162f9a8c49`. Model settings are available on the formal
  对话提炼 page. Real Chrome verified seven labels, saved selection, reopen/reload
  persistence and light/dark/mobile rendering. Selection changes used only the
  bounded local endpoint; the browser made no Core business writes.
- The operational audit records Sol → Luna (revision 2) → Sol (revision 3).
  The final default is gpt-6.1-sol. Invalid/stale/failed writes never claim success.
- A new invocation after the formal selection read the saved Sol configuration,
  called the real CLI and returned source-bound valid NO_STRUCTURE for the same
  genuine greeting. The Luna call took 14.03 seconds; this Sol call took 18.50
  seconds. These are individual smoke measurements, not latency benchmarks.
- All 41 pre-deployment Interpretation config hashes were preserved. There was
  no schema migration, approval, history rewrite or forced historical rerun.
- Core, the daily processor and dashboard restarted from the new release;
  deployment waited for model operations to be idle and used graceful termination.
  Hindsight retained its PID and continued running. Native Hook files, browser
  credential, Agent control profile and Memory provider configuration stayed
  byte-identical. No network/TUN/proxy or native-client login/default changed.
- The existing large frontend chunk advisory remains. No new full P0-P7 Gate or
  universal seven-model cloud-availability claim is made.
