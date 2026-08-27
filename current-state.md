# Current state — logistics bill product analytics

Last updated: 2026-08-26 (Asia/Shanghai)

## 2026-08-26 order notice and finance retry recovery

- The 2026-08-25 order chain ultimately completed at 20:37. The earlier “暂未进行订单更新” was an in-progress health message incorrectly routed to the order group, not the final order outcome.
- The order group now receives a completion receipt only after a fresh successful order closure with zero new images. Failed, stale, offline and password-pending states stay in the alert group.
- Wanshifu, Taobao aggregate balance, Wanxiangtai flow, enterprise Alipay balance and three enterprise Alipay bill days completed on 2026-08-25. Promotion OCR layout drift, WeChat bill empty/download handling, one enterprise Alipay bill-day gap and personal-Alipay login were separate failures.
- Personal-Alipay login no longer closes retries for other sources. Promotion OCR, WeChat bill, enterprise Alipay gaps and other automatic failures retain bounded 21:00/21:30/22:00 retries.
- The promotion reader supports both the old Wanxiangtai layout and the new “推广业务账户 → 万相台(元)” layout with strict anti-misread guards. A read-only production OCR check returned high confidence, an explicit Wanxiangtai label and a numeric value without writing or exposing the balance.
- ERP code/deployed commit: `c7389c9c2dffe85ce810ee24cb0da2858ae5b396`; GitHub documentation receipt: `683c720`. Web-Agent companion code commit: `eab594d7ddd679b7446076ff8aafb565b768ed64`; documentation receipt: `a9648f3`.
- Unified NAS verification passed health, ready, API/Web parity, migration `0139 (head)` and all four production containers. Rollbacks: `panse-system-api:rollback-20260826-002820`, `panse-system-web:rollback-20260826-003556`.
- Scheduler restarted with 62 jobs and no missed same-day critical shift. Primary-PC Wake Bridge is enabled/running, Watchdog is disabled, and post-release bridge polls returned HTTP 200 through the NAS.
- Remaining interaction gate: personal Alipay balance/flow requires re-login in the ERP automatic-data page. No business replay, live bill pull, login, password submission or external test notification was performed by maintenance task 02.

## 2026-08-25 Enterprise WeChat inbound shipping password

- GitHub `main` and deployed production code: `003f4a8ca76911681347b9c7c73b5bcc322cb881`.
- ERP now has a secure WeCom self-built-app callback at `/api/wechat/callback` plus an admin configuration card. It verifies signature/timestamp, AES ciphertext, CorpID, an explicit member UserID allowlist and message deduplication before entering the existing password -> decrypt -> ingest -> factory-image delivery recovery chain. Future shipping passwords, callback Token and EncodingAESKey are encrypted at rest and never returned by the admin API.
- Verification: changed scope `89 passed`, frontend build and unified NAS release passed. API/Web are healthy on the same commit, migration is `0139 (head)`, the callback route and deployed UI are present, and 62 scheduler jobs registered with no missed critical shift. Rollback images: `panse-system-api:rollback-20260825-131645`, `panse-system-web:rollback-20260825-132017`.
- The inbound feature is deployed but intentionally inactive: production has no `wechat_inbound_*` configuration and remains LAN-only. Activation still requires CorpID, Token, EncodingAESKey, allowed member UserID, and explicit approval for a public HTTPS callback route. No NAS exposure, account operation, password submission, order replay or external test notification was performed.

## 2026-08-25 operational notifications moved to Enterprise WeChat

- Screenshot contract is explicit in code: Web-Agent automatically requests the current shipping-report password, records bounded delivery evidence, never stores a password from that response, and keeps `too_frequent` as `sent=false` rather than claiming delivery.
- Taobao decides whether the password goes to the bound phone or main account. The existing Feishu ERP bot remains only the password input callback; ERP operational text and login QR images now go to Enterprise WeChat, while the Feishu factory chat remains image-only.
- ERP GitHub code commit/deployed production: `2d448172adee4e7511ed24d5c398c5fe9efa20b6`; documentation receipt: `367a05ca1a7a71b868956748585235e877fcefe7`. Web-Agent GitHub companion commit: `598993aef82badc04d761f1f6a196149f55860a7`.
- Verification: ERP related suite `85 passed`, Web-Agent full suite `91 passed`, Python compile and frontend production build passed. No real notification, password request, account action, or order pull was used for testing.
- Production API/Web are healthy on `2d44817`, migration is `0139 (head)`, 62 scheduler jobs registered with no missed critical shift, the Windows Wake Bridge is enabled/running, and the legacy always-on Watchdog remains disabled.

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
