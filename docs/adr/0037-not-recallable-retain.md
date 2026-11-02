# ADR 0037: A retain the engine extracted nothing from is NOT_RECALLABLE, not a failure

Status: implemented on `fix/memory-not-recallable` (schema 16); not yet deployed.
Amends ADR 0034 (observed admission) and ADR 0014 §5 (job states). It does not
change the rule that a retain completes only after the engine's unit listing
shows the memory.

Date: 2026-11-02. Schema: 16 (`m0016_not_recallable`).

## Context

Hindsight accepts a document and extracts memory units from it with a model. For
some short replies the extraction produces none: `不需要补录。`, `Leave as is`,
`可以。`, `不用重跑。`. The engine reports the document as stored with
`memory_unit_count: 0`, and the retain worker rightly refuses to call that
RETAINED (ADR 0014 §5, worker read-back).

Until this change such an item ended `RETAIN_FAILED` and its job `FAILED` after
its attempts were spent. On local-item6 that was 12 of 317 retain jobs, every one
of them this error code. Health reports `RETAIN_FAILED` as a failing Memory, so the
whole Memory subsystem looked broken while the pipeline was working as designed.

The retry is not wasted in general (extraction is a model call and a real
deployment once extracted nothing from content it later extracted), so the retry
stays. Only the terminal state is wrong.

## Decision

1. The retry budget is unchanged. A no-units retain is still retried.
2. When the budget is spent and the error is `engine_extracted_no_units`, the item
   becomes `engine_state = NOT_RECALLABLE` and its job becomes `DONE`, with a
   receipt `{"status":"not_recallable","reason":"engine_extracted_no_units"}`. The
   item keeps `last_error_code` and `safe_error_summary`, so the reason stays visible.
3. `NOT_RECALLABLE` is a terminal engine state. It is not counted as a failure in
   health. It is counted under its own key, `not_recallable`.
4. `NOT_RECALLABLE` items are never recalled, because they have no units. They may
   still be deleted or invalidated; those jobs accept the state as a predecessor.
5. Migration m0016 rebuilds `memory_items` to widen the `engine_state` CHECK, then
   converts the rows that match the rule (item and job) in the same transaction.
   Nothing is deleted and no identity column changes.

## Not claimed

* The extractor's behaviour on short text is not fixed here. Adding context to
  short replies before retain is a separate decision (ADR 0034 §3 would change).
* manual-reapply is not opened. An operator cannot yet change the text of a
  NOT_RECALLABLE item and retain it again.
