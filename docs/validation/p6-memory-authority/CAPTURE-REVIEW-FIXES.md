# Native Desktop conversation review fixes — 2026-10-19

Result: **PASS_DESKTOP_CAPTURE_EXPLICIT_RETAIN_SCOPED**. The two findings from the 9a200a1 review are fixed and validated in the isolated environment. The user's daily services were not changed. Machine evidence and source digests are in `CAPTURE-REVIEW-FIXES.json`.

## What changed

- **P1 / human attribution:** the observed Desktop input-suggestion request carries no subagent flags and uses a fork, but has `transcript_path: null`. The capture adapter now requires a readable persisted `session_meta` header whose native ID, working directory and main-session source match the hook. Missing, malformed, oversized, mismatched and subagent headers fail closed before registration, credentials are read, or event writes. The same check covers Stop, preventing a generated suggestion answer from becoming a normal assistant message. Persisted user forks remain eligible; no prompt keyword filter or blanket fork exclusion is used. Accepted events and private receipts carry the header digest and session source.
- **P2 / launch timeout:** the inventory reader now uses unbuffered byte reads and a persistent JSONL frame buffer. It drains complete lines before selecting the file descriptor, so a startup notification cannot strand the initialize reply. The owned app-server child and pipes are cleaned up on success, EOF, refusal, malformed input and timeout.

This adds a bounded read of the first transcript metadata record only (at most 1 MiB); it never imports conversation bodies from that file. OpenAI documents `transcript_path` as optional and its format as unstable: <https://learn.chatgpt.com/docs/hooks>. Unknown formats are intentionally excluded, including otherwise genuine ephemeral sessions. The persisted-header check is a local host provenance filter, **not authentication of a human or a universal classifier for future host automation**. The real manual input in this acceptance is additionally confirmed by the user in the controlling chat. Older misattributed immutable events remain preserved as historical audit evidence; they are not rewritten or promoted into this acceptance.

## Actual Desktop acceptance

The user manually opened a new chat in `jasmine mesh`, submitted the project boundary and explicitly confirmed saving it, then reported “已发送” in the controlling chat. It was not an initial delegation from `create_thread`.

| Record | Actual identifier |
| --- | --- |
| Native Desktop session | `01a15503-f4e5-79d1-8f5c-ac7656af2470` |
| Core session | `ses_01M5AG83CGMD7K3YMPQY32ZH3J` |
| Native user prompt | `evt_0ENJJHAR3B51EWPZMF9RC57TYR` |
| Native assistant answer | `evt_PFB92XJ9KRBV4P0RGMNMG6VTZQ` |
| Project decision | `rul_01M5AG92DGTMECYHJYZGZR99H6` |
| Operator approval / Memory source | `evt_01M5AG92DK5YBJD8ZHHQXMR6Y2` |
| Memory / job | `mem_01M5AG92S60NGG917Q41F8ZV2T` / `mjb_01M5AG92S72ST57J42YGT5AYB4` |
| Hindsight document | `hindsight:confirmed_decision:bedd635508d9499a625bdc1cfe82a2f1` |
| Extracted memory unit | `8994f5aa-5da5-4b50-a861-d713d257460b` |

The prompt was captured verbatim, including the user's surrounding formatting and explanatory sentence. The assistant answer is exactly `桌面验收消息已收到。`. Start, prompt and Stop share the same Core session and match native receipt digests. The subsequent real Desktop suggestion session `01a15504-2a81-73f0-b415-847a189e2573` generated ignored Start/prompt/Stop receipts and **zero Core events**. This is a real negative control, distinct from component fixtures.

Ordinary chat capture alone created no Memory. Based on the user's explicit confirmation, the operator proposed and approved the project-scoped Decision through existing APIs. The already-running worker retained it: job `DONE`, Memory `RETAINED`; direct Hindsight document and unit readback found one nonempty extracted unit. The retained text is `本次桌面验收只使用隔离环境，保留来源记录，不修改日常服务。` This proves the explicit approval path; automatic conversation interpretation/approval is not implemented by these hooks.

Actual browser checks verified the new session list entry, exact user/assistant text and ordering, folded lifecycle event, source details, retained Memory content/state/origin, the approval link and its destination. Browser traffic was GET-only. The first browser attempt looked for an ID before opening the source panel and timed out; its logs/screenshots are preserved separately. The corrected driver opens the existing panel and passes all 12 checks.

The manually created Desktop chat stays open, so its SessionEnd is not claimed. A separate real native CLI regression passed all four lifecycle callbacks and used the fixed inventory reader. Its operator-provided input is labelled as CLI automation, never substituted for the manual Desktop input.

## Validation and boundaries

- 22 Codex-related component checks passed, including five real subprocess framing/cleanup cases and nine capture cases. 197 existing host-hook regression checks passed. `git diff --check` passed.
- Actual trusted Hook inventory: four enabled/trusted definitions, read in 0.14 s. No trust definition or workspace configuration was changed by this repair.
- Isolated Core/Dashboard/Hindsight/PostgreSQL remained the same processes. Hindsight is still RUNNING, started at `2026-10-19T08:40:15.474659+00:00`.
- All 19 protected daily-service file hashes and four listener PID sets match the pre-existing baseline. No schema migration or daily-service write/restart occurred.
- Pre-existing uncommitted conversation UI changes from the other user-directed chat were preserved and excluded from this commit. The browser proof records the actual served UI; its assets are not represented as part of this backend patch.
- Prior immutable evidence is preserved. This result does not sign off automatic context injection, recall benefit, full P4/P6 or V1.
