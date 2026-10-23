# Local Claude desktop Code capture — 2026-10-23

PASS for local desktop Code: a fresh user-operated native session, its ordinary
message and completed reply were captured and shown on the formal Conversation
page after deployment. This does not claim Chat, Cowork or cloud capture.

The reported missing conversations were local Claude desktop Code sessions. Actual
native 2.1.308 records and private Hook receipts established the defect: main-session
human submissions used `entrypoint=claude-desktop`, `promptSource=sdk`, with explicit
`origin.kind=human` and `turnOrigin=human`. The adapter previously accepted only
`cli/typed` and ignored these Hook events as `no_current_typed_input_evidence`.

The adapter accepts the two exact entrypoint/source pairs. It still requires the
exact native prompt UUID, matching session/workspace, external human metadata,
private owned transcript, no sidechain, no meta-message and no tool-result assistant
reference. Generic SDK, mismatched pairs and automated/agent origins remain rejected.
The current Hook supplies all published message text; no previous desktop history
is imported. Native entrypoint and transport are retained in event provenance.
The Agent connection dialog states that this covers CLI and local desktop Code,
excluding ordinary Chat, Cowork and cloud sessions.

Validation:

- 23 capture checks passed across Claude, Codex and Grok, including desktop prompt
  and reply over test Core HTTP, replay/resume, origin rejection, timing races and
  installer preservation. These are component/integration checks, not native UI proof.
- 642 frontend/server checks and production typecheck/build passed. The existing
  large-bundle advisory remains.
- A read-only bounded metadata check against both actual desktop transcripts now
  recognizes the human turns previously rejected. It posted no messages to Core.
- Real Chrome verified the formal dashboard's scope text and enabled Claude profile,
  with no configuration drift, page errors or business writes.
- Deployment updates only Claude's pinned adapter and its four owned Hook commands;
  unrelated Claude settings/handlers are preserved. Codex/Grok Hook bytes and browser
  credential/device binding bytes are unchanged. Backups are private outside Git.
- Only the dashboard service restarted. Core and Hindsight retained their process
  IDs; there was no database/schema change or historical import.

Runtime source commit: `08ffea2149f99076861beb9707d2fb51d8cab3eb`.
Private receipts and browser screenshot are stored with the deployment backup.
Fresh native acceptance (the user confirmed sending the message):

- Native desktop session: `88c34a77-34ed-4b9d-bdb2-e506e7915702`.
- Core project: `prj_01M5KP8BKA8T5FPWC84JD9M44N`; session: `ses_01M5KP8BKK0KKPY12X0SNNC94M`.
- Matching accepted UserPromptSubmit and Stop receipts retain desktop/sdk provenance.
- Both Core message hashes equal their receipts and the actual native transcript text.
- Real Chrome displayed these exact two events on the formal Conversation route;
  rendered message hashes match Core, and the assistant has the Claude avatar.
- Browser verification was read-only and had no page errors. Historical messages
  were not imported; the earlier ignored sessions remain outside this new capture.

Reference: [official Claude Code Hooks](https://code.claude.com/docs/en/hooks),
which describes Hook events for both terminal and desktop Code sessions.
