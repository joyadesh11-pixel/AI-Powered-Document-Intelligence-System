# node: fail
**Purpose.** Terminal node for files rejected by `intake`. **Reads** `error_code`, `error`. **Writes** `status="failed"`.
Builds nothing else: `finalize` is skipped on this path, so `fail` must still let `process()` assemble a valid `PipelineResult` (empty invoices, `error_code`, history).
**Failure behaviour.** None; cannot fail.
## Tests
- T1 corrupt fixture -> result `status=failed`, `error_code=CORRUPT_FILE`, `invoices==[]`, history ends with `fail`.
- T2 result validates against `PipelineResult`.
**Done when** T1-T2 pass.
