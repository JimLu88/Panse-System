# Current state — logistics bill product analytics

Last updated: 2026-08-25 (Asia/Shanghai)

## 2026-08-24 order-export repair and live runtime audit

- ERP repair branch: `fix/order-export-current-batch`; code commit `ab3edba0e400c1fd0d873573298050ee2fd5f3d5` is present on the same remote branch.
- Web-Agent companion branch: `fix/order-export-resilience`; commit `97fb6c76f9dc792b376e884c208c62625b79b9ac` is present on its remote branch.
- ERP now limits order-only ingest and password checks to artifacts from the active export batch; an empty current-batch artifact list no longer falls back to scanning historical files.
- Web-Agent export detection now tolerates the current Taobao button/dialog variants and both legacy and current export POST endpoints.
- Verification: ERP relevant suite `73 passed`; Web-Agent full suite `90 passed`; Python compile and scoped diff checks passed. One unrelated pre-existing cross-midnight scheduler test remains outside this repair.
- Production is not changed by these branch pushes. Live API and Web remain `eed15b0b4b6e1244721673893e657900c2a003fc`; no merge, deployment, restart, account action, business replay, or external notification was performed.
- The production scheduler has no `scheduler_overrides` row. On 2026-08-24 it fired the order chain at 18:00 and catch-up at 19:17, 20:17, and 21:17; the schedule started normally, while the old production export code failed inside the Taobao trigger stage.
- The intended Windows runtime is on-demand: `Panse-Web-Agent-Wake-Bridge` is enabled and running; the legacy evening Watchdog remains disabled by design and must not be re-enabled as an always-on substitute.
- Fresh NAS checks passed: API `/api/health` and `/api/ready` returned 200, DB migration is `0139 (head)`, API/Web/DB/backup containers are running with zero restarts, SMB read plus create/write/flush/no-residue probe passed, SSH control path is reachable, and production Web-Agent `192.168.31.231:8500/healthz` returned 200.

To make the repair affect the next production order pull, merge both repair branches and perform the separately authorized ERP/Web-Agent release procedure. Branch push alone is not deployment evidence.

## Summary

The logistics bill page now shows product/SKU context and includes a read-only analytics tab for region, weight/volume band, product, product-region, monthly trend, carrier, and high-price anomaly analysis.

The feature is implemented, tested, committed, pushed, deployed to Synology NAS, and verified against production data.

## Current version

- Feature code commit: `c5aeed024bab7ff6b2c88ebba2423c946229e1e6`
- GitHub `origin/main`: contains the feature commit and may contain a later documentation-only archive commit; inspect it live with `git rev-parse origin/main`
- Production API: `c5aeed0`
- Production Web: `c5aeed0`
- Feature commit subject: `feat: add logistics product price analytics`
- Database migration at release verification: `0139 (head)`

Documentation-only commits do not require a container release. Production identity must be read from `/api/version` and `/build-version.json`.

Rollback images created before this release:

- `panse-system-api:rollback-20260813-163601`
- `panse-system-web:rollback-20260813-164144`

## Production evidence snapshot

Verified 2026-08-13 after unified release:

- Analytics endpoint HTTP 200 with real production data
- 183 logistics rows
- Total freight ¥51,405
- 107 order-matched rows
- 94 eligible single-product/single-quantity rows
- 2 multi-product shipments
- 9 same-product multi-quantity shipments
- 78 rows without product context
- Billing-weight coverage 99.5%
- Actual-weight coverage 68.9%
- Volume coverage 90.2%
- 2 anomaly alerts under the current rule

These are a dated snapshot, not constants.

## Completed decisions

- No database migration and no historical write-back for this feature.
- Imported child-order details are authoritative for product context.
- Refunded and service lines do not enter physical product analysis.
- Main order is only a fallback when imported details do not exist at all.
- A single-item average requires exactly one unique physical SKU and quantity 1.
- Multi-product and multi-quantity shipments are shown but excluded from single-item averages.
- Low sample sizes are visibly marked.
- Anomaly detection is advisory only.
- Filters are shared across all analytics outputs.
- Production deployment must release API and Web from one Git commit under the NAS lock.

## Verification status

- Backend related tests: 32 passed
- Frontend TypeScript/Vite production build: passed
- Python compile check: passed
- Scoped diff check: passed
- API and Web unified NAS release: passed
- GitHub remote commit match: passed
- Live analytics API and deployed bundle inspection: passed

## Open work

1. Resolve or classify the 78 rows without product context.
2. Apply non-physical/service filtering to the `Order` fallback without breaking real custom-product links.
3. Replace long/repeated marketplace titles with canonical ERP product names while keeping traceability.
4. Audit suspicious sample-block shipments with implausible weight/volume/freight.
5. Normalize full autonomous-region display names.
6. Add like-for-like trend analysis that controls province, weight/volume band, and carrier if the business needs price-change conclusions.
7. Consider export of the current filtered analytics with methodology and generated timestamp.
8. Revisit query aggregation/caching only when history grows enough to create measured latency.

## Current dirty-work boundary

At this update, the primary working tree also contains unrelated Tachikoma connection/AEO work and scratch artifacts. They are not part of the logistics feature and must not be staged, overwritten, deleted, or deployed accidentally. Always re-run `git status --short` because this list can change.

## Where to continue

Start with `docs/logistics-bill-product-analytics.md`. For the next engineering task, the safest first target is the 78 unmatched rows plus the service-link leakage test, because both directly affect analysis credibility.
