# Explicit partial order job adoption

Owner: ERP 04 maintains the entry; ERP 01 executes it. Business date is explicit,
including when collection occurs after midnight. No automatic task is added.

The Web-Agent capture script reads one authenticated terminal job and hashes two
completed artifacts. Its immutable source, exclusive claim and new attempt receipt
remain under agent_output/order-recovery and order-runs. Original attempts are never
relabelled. Collection runs only the deterministic shipping collector: no export,
quota submission, model fallback, old-row selection or repeat claim.

After the collector returns a complete receipt, invoke the installed ERP module:

    python -m app.cli.order_job_adoption --attempt NEW_ATTEMPT --receipt-sha256 RECEIPT_SHA --source-job-id SOURCE_JOB --business-date YYYY-MM-DD --previous-attempt ORIGINAL_FAILED_ATTEMPT

This default is validation only. ERP 01 adds --apply to claim the source once and
use the existing exact-manifest importer. It does not dispatch factory messages.
Password handling and downstream delivery remain the existing business owner's
flow. A claimed, interrupted or blocked operation is not rerun automatically.

Guards: complete three distinct roles/hashes, unchanged source pair, exact day/path,
verified source quota, matching new batch/attempt/claim, no active orchestration,
unchanged prior attempt and no newer-day receipt overwrite. Database advisory
transaction lock serializes adoption claims. Local and production evidence are
reported separately; tests are not proof of shipping collection or import.

Relationship impact review: the new service/CLI are currently reported as unmapped
by business_relationships. They feed existing receipt evidence, quota evidence and
run_ingest with only the validated three files. This affects imported-order freshness
and the shipping-password gate. Existing ingest reconciliation, factory-row sync
and password reminders remain unchanged; the new entry adds no export, scheduling,
factory dispatch, campaign operation or price change. Existing catalogue fingerprints
are preserved; absent mappings are not a claim of no downstream effect.

## Confirmed shipping quota failure continuation

The user screenshot of application 2026-10-03 00:03:53 identifies shipping and
decrypt quota exceeded; exact-row metadata links export 27216774510. The older
quota check occurred before midnight. A daily platform reset remains a hypothesis.
Web-Agent now checks the live quota console before every shipping submission,
including single-report runs, and blocks if China date changes before submission.

Only the reviewed source job2-f0f7d0df859ca8013388081d5a8abf35 and failed receipt
45f0488f0049499487651dd1c5aa201f may use shipping_quota_failure_recovery_v1.
The source-wide quota-recovery claim is separate; original collect claim and
failed receipt remain immutable. One new shipping trigger only, no model retry,
no original-file re-export. Login or verification stops at the existing gate.
The same ERP CLI validates both receipt kinds, but this new kind requires the
old failed receipt digest, both claims, same-day live quota, response digest and
business status, and a distinct fresh export ID before importing exactly three
files for business date 2026-10-02. No factory message dispatch is introduced.

Relationship review: this service remains explicitly unmapped by the catalogue.
Changed inputs are quota checked_at and new-receipt provenance; no order field,
quantity, inventory, financial recalculation or delivery permission changes.
Original ingest password/reconciliation/factory-row effects still apply. No
unrelated source fingerprint is refrozen to silence the uncovered mapping.
