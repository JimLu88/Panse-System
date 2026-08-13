# Panse-System agent instructions

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
