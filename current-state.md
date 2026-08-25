# Current state — logistics bill product analytics

Last updated: 2026-08-25 (Asia/Shanghai)

## 2026-08-25 campaign signup one-shot hardening

- The former `qualify_signup_scope` upload probe is disabled. QianNiu creates a real batch operation when the signup workbook is attached, so the automation now performs local/read-only evidence checks and exactly one final campaign signup submission.
- Every signup row has a required relative shipping time. The policy default is `30天`; exact item overrides remain possible, but blank rows and bare numbers are rejected.
- Campaign I/O now requires the immutable title, `campaignId`, `unitedActivityId`, exact start/end timestamps, and official discount rate. Feedback and post-submit exports are read from that exact identity, not a title-only activity list search.
- Historical no-sales registration is advisory before submission. After the one terminal signup result, only exact items whose sole failure is no-sales receive the no-official-discount single-item fallback. Hard failures and incomplete/stale feedback are isolated and stop without a second campaign submission.
- Completion requires terminal item counts, exact failed-item scope, fresh exact-ID export, and per-SKU signup-price verification. A bounded, non-secret structured receipt records the job ID, terminal counts, feedback artifact, export SHA-256, per-SKU verification, and fallback result.
- Invalid no-sales registry values such as `5`, `待定`, and `暂无` are ignored. The public write-probe entry and Web-Agent `promo_signup` stage route both fail closed.
- The primary Windows host demonstrated a cold-start time beyond the former 150-second bridge limit. The Web-Agent bridge now waits 360 seconds and ERP callers wait 420 seconds, so a slow healthy launch is not falsely marked failed; the Agent remains on-demand and the legacy Watchdog stays disabled.
- Verification for this change: campaign-related ERP suites passed; Web-Agent full suite passed 93 tests with the required async test plugin loaded temporarily. The broader ERP suite still has unrelated pre-existing failures outside campaign files; no live campaign submission, account action, price change, withdrawal, or business retry was used for testing.

## 2026-08-25 Enterprise WeChat group-chat shipping password

- GitHub `main` code commit and deployed production: `89f00c60c5b9cafe672878e88ee5a26f7bb3c4a1`.
- Added the Enterprise WeChat intelligent-bot JSON/AES callback at `/api/wechat/aibot/callback`; the previous self-built-app single-chat callback remains at `/api/wechat/callback`, with separate credentials.
- Group messages are accepted only after protocol signature/AES validation and member UserID allowlisting. Supported forms include `发货密码xxx`, whitespace/colon/equal variants, `密码xxx`, and `口令xxx`. A bare ASCII password is accepted only after the exact configured bot `@name` is removed from an authenticated intelligent-bot callback. Passwords are never echoed or logged; message IDs are deduplicated.
- The callback uses the authenticated one-time WeCom `response_url` to acknowledge receipt in the originating chat, while the existing password -> decrypt -> ingest -> manifest closeout -> pending factory-image delivery chain continues asynchronously.
- Verification: relevant callback/config/notification/order-isolation scope `32 passed`; Python compilation, frontend production build, shell syntax, and diff checks passed. Public HTTPS reached the new callback with the expected HTTP 422 when protocol signature parameters were intentionally omitted.
- Unified NAS release passed: health, ready, API/Web commit parity, migration `0139 (head)`, both callback routes, deployed group-bot admin UI, and all API/Web/DB/backup containers verified. Rollback images: `panse-system-api:rollback-20260825-141943` and `panse-system-web:rollback-20260825-142233`.
- Scheduler restarted with 62 registered jobs; the delayed startup catch-up check reported no missed critical shift.
- Activation is waiting only on the WeCom account gate: production `wechat_aibot_enabled=false`, and bot Token/AESKey/name are unset; the existing allowed-member list remains set. The user must create an internal intelligent bot under WeCom “安全与管理 → 管理工具 → 智能机器人”, configure the public HTTPS callback plus `/api/wechat/aibot/callback`, save the same credentials/name in ERP admin, pass URL verification, and add the bot to the target group. No account action, real password submission, business replay, or external test message was performed.

## 2026-08-25 Enterprise WeChat inbound shipping password

- GitHub `main` code commit and deployed production: `003f4a8ca76911681347b9c7c73b5bcc322cb881`.
- ERP now exposes a WeCom self-built-app callback at `/api/wechat/callback`. It verifies the protocol signature and timestamp, decrypts AES messages, verifies CorpID, enforces an explicit member UserID allowlist, accepts only an explicit `发货密码` command, and persists message IDs for retry deduplication before invoking the existing password -> decrypt -> ingest -> exact manifest closeout -> factory-image delivery recovery chain.
- Callback Token, EncodingAESKey, and all future `taobao_shipping_pwd_latest` values are encrypted at rest and never returned by the admin API. The admin console contains a separate “企业微信接收发货密码” configuration card; the outbound group-robot webhook remains independent.
- Verification: callback/config/security and order-recovery scope `89 passed`; Python compile, frontend production build, shell syntax, and diff checks passed. The unrelated legacy full-suite failures were reproduced individually and were not changed in this repair.
- Unified NAS release passed: health, ready, API/Web commit parity, migration `0139 (head)`, callback route, deployed admin UI, and API/Web/DB/backup containers all verified. Rollback images: `panse-system-api:rollback-20260825-131645` and `panse-system-web:rollback-20260825-132017`.
- Scheduler restarted normally with 62 registered jobs and no missed critical shift.
- Activation remains intentionally closed: production has no `wechat_inbound_*` configuration, and the ERP is currently LAN-only. The code is deployed but direct WeCom message input is not active until the user supplies/configures CorpID, callback Token, EncodingAESKey and allowed member UserID, and explicitly approves a public HTTPS callback route. No NAS exposure, account operation, password submission, business replay, or external test notification was performed.

## 2026-08-25 operational notifications moved to Enterprise WeChat

- Screenshot contract is now explicit in code: Web-Agent automatically requests the current shipping-report password through Taobao's `data-secretquery` endpoint, records only bounded delivery evidence, and never stores a password from the response. `too_frequent` remains `sent=false`; it proves only that a recent request was triggered, not that the user received a password.
- Taobao, not ERP, selects whether the password goes to the seller's bound phone or main account. After the matching password is provided to the existing Feishu ERP bot input, ERP immediately retries decryption, imports the current shipping report, closes the exact order-pull manifest, and resumes pending factory-image delivery. Passwords are not rejected by age; mismatches stay pending without blind retries.
- Operational text, login-expiry notices, and login QR images now go only to the configured Enterprise WeChat webhook. The Feishu factory-order chat remains image-only. Scan recovery instructions point to the ERP automatic-data page instead of asking the user to reply `扫码` in Feishu.
- ERP GitHub `main` code commit: `2d448172adee4e7511ed24d5c398c5fe9efa20b6`. Web-Agent GitHub `main` companion commit: `598993aef82badc04d761f1f6a196149f55860a7`.
- Verification: ERP notification/password/order-recovery suite `85 passed`; Web-Agent full suite `91 passed`; Python compile and frontend production build passed. No real notification, password request, account action, or order pull was used for testing.
- Production configuration was checked without exposing the webhook: `notify_provider=wechat_work` and the webhook is set. ERP API and Web were released together from `2d44817`; health, ready, commit parity, migration `0139 (head)`, required routes/features, and API/Web/DB/backup containers all passed the official NAS release verification. Rollback images: `panse-system-api:rollback-20260825-123500` and `panse-system-web:rollback-20260825-123947`.
- Production scheduler restarted normally with 62 registered jobs and reported no missed critical shift. The Windows on-demand Wake Bridge remains enabled/running, the legacy always-on Watchdog remains disabled, and the bridge continues polling the NAS successfully. No business replay was triggered.

## 2026-08-25 factory dispatch integrity repair

- Incident cause: the automatic ERP -> Feishu factory projection had not completed successfully since 2026-08-15. Two missing wood-cost values blocked the whole table, while the six-hour scheduler wrapper incorrectly recorded the inner factory failure as `ok`.
- Shipment-note cause: the old rule treated seller memo `开始制作` as both production release and shipment approval. A buyer instruction such as `延迟发货 发货前通知` therefore fell through to `做好直接发货`.
- Repair commits on GitHub `main`: `ceaa3a7cfc52adadbba81190556a90506303ca85` (projection/filter/note/scheduler repair), `abc1feb214bff290380fe1e649d1c30487ef10b6` (bounded Feishu delete retry), and `5b05e4dc6e6e2c509ead6d92ab7bf202a2f88514` (prevent delete/recreate loops when a Taobao child ID overlaps an active main-order ID).
- Production API and Web are both deployed from `5b05e4d`; `/api/health`, `/api/ready`, the reverse proxy, migration `0139 (head)`, and API/Web/DB/backup containers passed the official NAS release verification. Rollback images: `panse-system-api:rollback-20260825-014342` and `panse-system-web:rollback-20260825-014705`.
- A pre-correction Feishu backup is stored on NAS at `/volume1/docker/panse/storage/_ops_backups/factory_dispatch_before_integrity_sync_20260825T012526+0800.json`, mode `0600`, 260 records, SHA-256 `2918a7f0288df501b743c83938f8babd142e7a1c71a2a273b2dc46f820180842`.
- Live correction removed 58 ERP-confirmed refunded/cancelled records. Final reconciliation: 221 projected rows, zero missing projected entities, zero duplicate entity keys, zero invalid remote rows. Three non-projected/manual-or-blank rows were preserved because their origin was not safe to infer.
- Factory order 368 now has exactly one live row and `发货安排=做好后等通知发货`. Two consecutive post-release syncs both returned `ok=true`, `created=0`, `updated=0`, `deleted_ineligible=0`.
- Missing wood cost remains a visible warning for 2 orders but no longer blocks status, refund cleanup, or shipment-gate updates. The values were not guessed or written.
- Final changed-scope regression: factory/order-note tests 51 passed; Feishu tests 43 passed and 2 skipped; Python compile and diff checks passed. The full backend suite still contains 27 pre-existing unrelated failures; representative failures were reproduced on the clean pre-repair baseline.
- Scheduler runtime: `feishu_sync_30min` is enabled with no override at 360 minutes. The deployed API registered 62 jobs and completed its startup catch-up check with no missed critical shift.
- Windows on-demand bridge verification passed end to end on the primary PC `192.168.31.91`: NAS requested a start, Web-Agent became HTTP 200 from both NAS and Windows, then an explicit stop restored the intended idle state. `Panse-Web-Agent-Wake-Bridge` remains running; the legacy always-on Watchdog remains disabled.
- Upstream freshness is a separate remaining business gate: the last successful Taobao report timestamp is 2026-08-23 18:27. Web-Agent GitHub `main` and the runtime order orchestrator both contain `97fb6c76f9dc792b376e884c208c62625b79b9ac`, but a fresh Taobao order pull was not triggered from maintenance task 02. That pull/login interaction belongs to task 01 before claiming newly changed marketplace refunds are current.

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
