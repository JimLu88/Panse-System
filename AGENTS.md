# Panse-System agent instructions

## Failure handling confirmed 2026-09-12

Read `docs/campaign-failure-remediation-20260912.md` for automatic failed-scope
continuation. The user directly authorized recurring custom reductions within
the established fixed 20% floor and ordinary final-price delta <=2 CNY. Do not
ask again for those. No-sales failures are skipped for this campaign; invalid
or disabled SKUs require one recorded full export/ID-code reconciliation, then
file-only exclusion of proven ineligible SKUs. Rotation remains a human gate.
These clarifications supersede old per-attempt price-approval wording only in
the continuous flow; original successful/unknown claims stay protected.

## Current continuous-campaign migration (user approved 2026-09-11)

User additionally approved segmented timing: read
`docs/campaign-segmented-time-20260911.md`. Super-reduce platform validity is
NOT its single-discount window. New timed bundles use M in daily gaps and B
in exact big-campaign windows; preserve old hashes/claims and successful offers.
Already enrolled super-reduce items may need a new discount segment but MUST
NOT be enrolled again. This is pure generation/controller logic, no preflight.

Read `docs/campaign-continuous-flow-20260911.md` for new automatic signup work.
The user-approved new flow supersedes conflicting old preparation steps for new
runs: ERP sellable intersect Taobao on-sale, fixed official discovery entry,
no historical preflight, continuous changed-failure repair, human-approved rotation.
Preserve old claim/rule fingerprints for existing batches. The new controller
is NOT yet connected to verified live Web-Agent transport. Do not restore the
retired preflight pipeline or advertise fake/offline adapters as production-ready.

## Browser failures (not a campaign preflight)

After a browser-control failure, use `docs/browser-control-short-recovery.md`.
Keep the current authorized Edge session and sole business-page owner. Distinguish
caller timeout/kernel reset, stale tab, ownership conflict, debugger detach,
policy failure and file-transfer failure. Unknown action outcomes are not retries.
Use bounded single-stage calls and fresh semantic page evidence; never invent a
connect button, reset profiles, open full CDP, or change frozen campaign rules.
`scripts/browser_control_triage.py` is offline advisory classification only, not
a mandatory signup gate or a browser runtime replacement.

## Campaign signup authority

For the current user-established 78+4 custom baselines, read
`docs/campaign-user-established-baselines-20260911.md`. Use the 78-row v2 receipt;
the v1 precision error is retained only for audit. Fixed original times 20% is
exact; a fractional-cent floor is rounded UP only for the submittable price.
Do not ask again for this established baseline or rebase after later reductions.

For the current failed ordinary-SKU existing-discount amendment only, use
`docs/campaign-discount-amend-20260911.md`. The local claim/record guard produces
an exact in-place-edit payload, not a new offer or bulk replay. The browser owner
must supply real old-value and post-save readbacks; unknown remains blocked.

Current 2026-09-11 user authorization: see `docs/campaign-autumn-two-yuan-20260911.md`.
Only that exact autumn campaign/window/12%/big target accepts actual reused final
price absolute delta <= 2 CNY. Generation and claim must share the pinned receipt.
This is not a global tolerance or instruction to subtract two; custom floors,
ordinary daily signup and success/unknown replay protection remain unchanged.

Local campaign files/submission use `docs/campaign-entry-guards.md`:
`campaign_generate_current_files.py` with exact `--campaign-key`, followed by
`campaign_submission_gate.py claim/record` (or `run_once` in the existing owner
transport). Keep the same persistent authority SQLite across conversations.
Pure `build_rows`/byte writers and raw browser/API calls are not fully guarded
business entry points. Do not claim this change intercepts those bypasses.
Reused discounts must be checked using actual successfully uploaded amounts,
not the newly calculated but unsubmitted ideal deductions. No tolerance expansion.

Single-discount template is the user's fixed 2026-09-09 master:
`D:/AI/畔色ERP系统/活动准备/固定模板/单品立减-SKU级-固定官方模板.xlsx`.
Never download it again or ask the user to do so. Fill a new copy using current
SKU/amount rows; never reuse previous filled rows. Activity signup templates
still require the current exact campaign's official download. See the frozen
contract's single_discount_template field; prices/windows/short flow unchanged.

For campaign work, first read `docs/campaign-signup-frozen-steps.md` and
`docs/campaign-signup-frozen-contract.json`. They record the user's September 4–6
short-flow rules and exact successful scope; older preparation/recovery documents
are history, not authority to restore preflight scans or replay successful batches.
Routine signup belongs to `01｜畔色ERP系统`; maintenance does not execute it.
Do not change frozen rules without the user's current direct instruction.
The offline receipt tests are maintenance verification, not a new signup gate.
The user now communicates through 02; 01 remains the sole business/browser writer.
Necessary custom reserve work requires the current scoped instruction. Prefer
verified existing inactive reserves, preserve stock and the first-original 20%
floor across exact old/new SKU mappings. Unknown inventory is not a missing SKU;
ordinary SKUs and successful campaigns are never implicitly rotated or replayed.

## First read

Before modifying the logistics-bill product analytics feature, read:

- `current-state.md`
- `docs/logistics-bill-product-analytics.md`
- `scripts/lib/nas_deploy_guard.sh`
- `scripts/deploy_release_nas.sh`

Treat production state, database contents, current Git status, and live API/Web versions as mutable. Re-check them instead of assuming the documentation is current.

## Repository safety

- This repository is edited from multiple conversations and sometimes multiple computers.
- Preserve unrelated modified and untracked files. Never use `git add -A`, `git reset --hard`, `git checkout -- .`, or broad cleanup.
- Stage and commit only explicitly scoped files.
- Before production deployment, use a clean worktree at the exact `origin/main` commit. Do not deploy a dirty primary working tree.
- Do not remove a NAS release lock unless its owner and the other deployment have been investigated.

## Logistics analytics invariants

- `OrderDetail(source="import")` is authoritative when present; `Order` is only a compatibility fallback when no imported detail exists.
- Exclude service/installation/freight/price-difference and refunded lines from physical-product analytics.
- If imported details exist but all are refunded or filtered, do not fall back to the main-order product.
- Only one unique physical SKU with total quantity exactly 1 may enter single-item freight averages.
- Multi-product shipments and same-SKU multiple quantities remain visible but must not enter single-item averages.
- Never allocate a full-shipment freight charge across products without an explicit, auditable allocation rule.
- Weight bands use billing weight; actual weight and volume are separate fields. Missing values are not zero.
- Samples under 3 must remain visibly low-confidence.
- Current anomaly rule: same product + SKU + province, at least 3 samples, freight at least 1.5× median and at least ¥30 above median. It is an alert, not an automatic accounting correction.
- Trend comparison is against the previous month with data, not necessarily the immediately preceding calendar month.

## Relevant files

- `backend/app/services/logistics_analytics_service.py`
- `backend/app/api/finance.py`
- `backend/tests/test_logistics_analytics.py`
- `backend/tests/test_logistics_bill_match.py`
- `frontend/src/pages/LogisticsBillsPage.tsx`
- `frontend/src/components/LogisticsAnalyticsPanel.tsx`

## Required verification

For changes to this feature, run at minimum:

```powershell
cd D:\AI\Panse-System\backend
python -m pytest -q tests/test_logistics_analytics.py tests/test_logistics_bill_match.py tests/test_automation_features.py

cd D:\AI\Panse-System\frontend
npm run build
```

Also run `python -m py_compile` for changed backend modules and `git diff --check` for scoped tracked files.

Do not claim production completion from tests or a successful image build alone. Verify separately:

1. GitHub `origin/main` contains the intended commit;
2. NAS API and Web report the same commit;
3. `/api/health` is healthy;
4. `/api/finance/logistics-bills/analytics` returns the expected schema and current data;
5. the deployed frontend bundle or visible page contains the new UI.

## Deployment

The only normal unified NAS release entry is:

```bash
bash scripts/deploy_release_nas.sh
```

Do not deploy API and Web from different commits. Preserve rollback image names and report them. Production OpenAPI documentation is disabled; a 404 from `/api/openapi.json` is not route evidence.

## Known follow-up work

See `current-state.md` and the full project document. Highest-priority gaps are unmatched product rows, service-link leakage through the main-order fallback, canonical product naming, and suspicious historical sample-product matches.
