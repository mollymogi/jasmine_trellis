# Engine status clarification — isolated Dashboard

The health overview now distinguishes a running Hindsight service from unverified Memory operations. Previously, the running service replaced the start button with a badge while the main title still said pending verification. The start button now stays visible and disabled while running; the title says “Hindsight 已启动” only when the controller reports RUNNING and Core reports configured, reachable, idle, and no Core impact. Failure and unknown verdicts remain prominent. A failed status read withdraws the running claim.

Validation on 2026-10-19:

- Frontend: 580 tests passed; production build passed (existing bundle-size advisory).
- Actual isolated Dashboard: 17 browser checks passed, including visible disabled start button, unverified Memory wording, desktop/mobile light/dark rendering, detail navigation, contrast, and GET-only requests.
- Live Hindsight 0.10.2 remained RUNNING, started at 2026-10-19T08:40:15.474659+00:00; Core reported configured=true, reachable=true, idle, last_success_at=null.
- No engine restart, retain/recall operation, automatic write activation, schema change or production service change was performed. Daily service listener PIDs and 19 configuration/launcher file hashes matched the prior baseline.
- Existing Memory records remain synthetic fixtures; they are not evidence of successful operations against this running engine.

Raw receipts and four screenshots are in the isolated work root under `evidence/engine-status-clear-20261019/`; source and receipt hashes are in the adjacent manifest.
