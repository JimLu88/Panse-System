# Current state — logistics bill product analytics

Last updated: 2026-08-30 (Asia/Shanghai)

## 2026-08-30 campaign automation failure-history closeout

- The former plan-7 "existing draft publish" interpretation is retired.  A row
  in the official enrolled-item export with status `暂停` is an enrolled but
  paused record, not a platform draft.  The ERP and Web-Agent publish routes now
  fail before browser work; the historical snapshot is corrected by migration
  `0145`.  All older sections below remain incident history only and must not be
  used as current operating instructions.
- Every final campaign upload now has a database-backed immutable attempt keyed
  by workflow, operation and exact manifest hash.  A write claim is committed
  before the browser upload.  After that claim, success, failure and unknown
  outcomes are all no-retry, including after container/process restart.
- Immediately before the claim, ERP downloads the official current-product
  workbook and requires the full exact SKU set for every pending product.  A
  missing/new/extra SKU, stale mapping, nonnumeric identity or workbook parsing
  gap stops before platform write.  Merged blank continuation rows are
  forward-filled so additional official SKUs cannot disappear from the guard.
- Read-only connectivity/startup failures before any write claim retain the
  current plan state and may retry in the next scheduler window.  Login, QR,
  CAPTCHA, campaign identity, SKU, price, no-sales and policy failures remain
  hard stops.  Automatic discovery now persists a stable workflow key for every
  exact campaign/phase/window so scheduler restarts cannot create a second
  identity.
- Price rules are unchanged: real-SKU signup price equals ERP daily price; final
  price must equal the ERP mid/big target; no price reduction, SKU rotation,
  withdrawal, pause or removal is introduced.  Known no-sales items are kept
  out of campaign signup.
- Historical review covered 76 campaign API audit rows, all 48 failed scheduled
  campaign runs, 15 reconciliation reports, 9 evidence snapshots and the NAS
  campaign artifact series since July.  The durable failure taxonomy and the
  prevention mapped to each class are recorded in
  `docs/活动报名全量失败复盘与自动化闭环_20260830.md`.

## 2026-08-30 plan 7 exact existing-draft publication entry

- The only permitted scope is the two platform drafts created by attempt
  `782299846f10d86ef4742c20`: items `717809819543` and `793084818113`, 29 SKUs,
  immutable price fingerprint
  `0355c293c277330e490858df4f6b4bb57484881fcea9897f27c194b68fb7231b`.
- Before the irreversible claim, a fresh full enrolled export must prove that
  every `暂停` item on the long-running Super Reduce sign record is exactly one
  of those two items, and that all 29 SKU IDs/prices still match snapshot `9`.
  One extra draft, one missing SKU or one price/status drift stops before the
  publish click.
- The entry never uploads a workbook, changes a price, rebuilds a draft,
  withdraws/pauses/removes an item, touches the other three failures, plan 8 or
  the 388 single-discount rows. It claims once before the platform confirmation;
  unknown/failure outcomes are permanently no-retry. A successful confirmation
  still requires a fresh 29-SKU readback in `已发布设定`/`活动中` state.
- Task 01 owns the sole business invocation through
  `scripts/campaign_publish_plan7_existing_drafts_nas.ps1`; maintenance only
  ships and verifies the program and must not run the command.

## 2026-08-30 plan 7 partial draft-import audit

- The one claimed five-item job returned the official terminal `5 total / 2
  import-success / 3 failed`. Super Reduce stops before `一键发布` whenever
  import failures exist, so this is a partial platform draft write, not zero
  platform activity and not proof that the two items are enrolled.
- Attempt `782299846f10d86ef4742c20` and manifest
  `2fa747d77823ed63baee82c5dbcc0d0fff6e248f77583dd4c9b074fa57d5c30d`
  are permanently no-retry. The fixed audit entry downloads the original
  failure workbook, re-exports enrolled items, verifies all five per SKU and
  recomputes final-price math. Evidence is append-only and every execution
  boundary remains false.
- No failed-only recovery is exposed by this release. Exact draft/enrolled
  status and all three official failure reasons must be known first; the two
  draft-import successes, any already-enrolled item and every price conflict
  are forbidden from another upload.
- The one permitted audit is complete as snapshot `9`. Official feedback
  SHA-256 is
  `699a962e46d8a68daaeaabd3a29d1322c337510f68c5339f7d8983f5aaeabaae`.
  Items `717809819543` and `793084818113` were imported only into platform
  draft; every expected SKU price matches exactly, but every row is `暂停` and
  neither item is proven active. Items `1036273574687`, `1074244132390` and
  `793202812082` failed respectively because the upload omitted current
  platform SKU `6280268983409`, the official coupon-after price ceiling would
  require a lower signup price, and official 60-day sales were zero.
- The audit performed platform reads only and recorded `platform_write=false`,
  `account_action=false`, `price_change=false`, `sku_rotation=false`,
  `notification=false` and `automatic_retry=false`. Failed-only recovery
  remains closed: publishing could also affect the two existing drafts, the
  missing SKU has no trusted ERP daily-price mapping, and the other two failures
  are hard price/no-sales gates. The attempt must not be rerun or described as
  a completed signup.

## 2026-08-30 plan 7 fresh official-scope partition

- Fresh official export SHA-256
  `0faefce8f97c1b470fefcea4ece33bfc62c14beaf5471c9b88826ac8bee718c0`
  contains 25 of the formerly reviewed 30 items. A product-name hit is not
  success: every expected SKU was compared to its exact activity price, and
  real-SKU final price was recalculated from official plus existing discount.
- 21 items are exact and are read-only closeouts. Four existing items are hard
  stops and cannot be uploaded or repriced: `1046992283533`, `717418169535`,
  `840643621692`, `840659847455`. Five wholly missing items are the only new
  upload scope: `1036273574687`, `1074244132390`, `717809819543`,
  `793084818113`, `793202812082` (70 rows: 52 real + 18 placeholder; scope
  SHA-256 `1f66d114e711b0fb3448a8a1503120bb5edd35a2d6416105f66545392f15bc86`).
- The recovery entry re-exports and re-partitions all 30 before claiming. Any
  drift stops without a write. Only the exact five missing whole items can be
  claimed; each still requires R16/R17, a platform terminal and exact per-SKU
  post-submit export. The four conflicts stay in the plan hard-failure marker,
  so the plan remains alarmed after the safe five complete. The old incident
  payload is rejected.

## 2026-08-30 plan 7 pre-claim export recovery hardening

- The second plan-7 remaining-signup request reached ERP and returned HTTP 409
  after about 394.6 seconds, but the caller session disappeared before showing
  its JSON. Live state proved the one-shot setting was absent and no execution
  receipt was added, so no batch was claimed and no platform write occurred.
- The fixed payload is now bound to recovery incident
  `plan7-preclaim-export-e222849772c5`; stale copies of the old command fail
  schema/request guards. While the server is working, the container CLI emits a
  heartbeat every 20 seconds so SSH/Codex does not appear silently stalled.
- Any future pre-claim evidence failure is persisted as
  `plan7_remaining_preclaim` evidence with `attempt_claimed=false` and
  `platform_write=false`, while the one-shot attempt key remains untouched.
  Focused route/service/CLI regressions passed 25. No campaign execution was
  used for verification.

## 2026-08-30 plan 7 submitted identity-recovery readback closeout

- Recovery attempt `ab51f002ac5570c9bb407d00` has an authoritative platform
  terminal: four rows validated, four imported successfully, zero failed and
  `submitted=true`.  It must never be uploaded or submitted again.
- Its immediate readback failed before reading rows because the persistent
  browser's first restored tab was Product Center, where `活动列表` correctly
  had zero matches.  This was a readback page-binding failure, not a platform
  import or pricing failure.
- The dedicated closeout route/CLI is fixed to the same workflow, plan,
  attempt, terminal evidence request and four-row scope.  It contains no upload
  call, claims one readback receipt before contacting Web-Agent and updates the
  existing attempt rather than creating a business attempt.  Completion still
  requires current SKU IDs `6127845548093` through `6127845548096`, exact
  deductions 612.63/627.08/644.50/662.44, status `未开始` and an immutable fresh
  artifact.  Any difference is terminal and cannot retry automatically.
- Focused ERP closeout/service tests passed 28; all 16 campaign test files
  passed with two existing fixture skips.  Maintenance did not execute the
  readback command or any platform write.


## 2026-08-30 plan 7 stale Taobao SKU identity recovery

- The first exact four-row correction stopped safely with platform terminal
  `参数错误:skuId不是商品的有效sku` for all four rows.  No row was accepted,
  `submitted=false`, and the existing 384 exact rows were untouched.
- A fresh official one-item QianNiu product export proves that merchant SKU
  codes `PFG2521002122211` through `PFG2521002122214` still represent the same
  four table sizes, but their current Taobao SKU IDs are `6127845548093`
  through `6127845548096`; ERP still held obsolete IDs `6279984722445`
  through `6279984722448`.  This is a one-to-one external identity repair, not
  SKU rotation.  The official artifact SHA-256 is
  `cdf6502bbf4c048824a0ad5f1545d6335faa117a854f3c624773c1e610a9a72b`.
- `POST /api/campaigns/recover-super-reduce-plan7-discount-sku-identity` and
  its controlled container CLI are fixed to workflow plan 7, item
  `1047741902625`, the failed attempt, immutable snapshot 1, the exact nine-row
  official export and four fixed merchant-code mappings.  They CAS-repair only
  those four external IDs, preserve every price/specification, verify the new
  388-row digest, perform one exact read-before-write and allow at most one new
  four-row import.  Every terminal is claimed, automatic retry is forbidden,
  and a successful terminal still requires a fresh exact post-readback.
- The route cannot change price, rotate SKU, touch plan 8 or the existing 384
  rows, call campaign signup, withdraw/pause/remove anything, notify, or accept
  a different export.  The identity evidence and official workbook are stored
  immutably before the platform phase.
- Verification passed: 28 focused identity/correction/service tests, all
  campaign tests (223 passed, 2 intentional fixture skips), Python compilation,
  PowerShell parsing and diff checks.  Maintenance did not execute the recovery
  command or any platform write.


## 2026-08-30 plan 7 exact four-row single-discount correction

- Immutable snapshot `1` (`plan7-discount-audit-464fc409dce0`) proves that the
  388-row target was not wholly missing: 384 rows are present in activity
  `143780562424`, all at the exact ERP deduction, and all have platform status
  `未开始`.  Because their fixed window begins at `2026-09-01 00:00:00`, these
  384 rows are correctly configured and waiting for the window; they must not
  be resubmitted or called effective before the start time.
- The only missing scope is item `1047741902625`, four physical SKUs:
  `6279984722445` (daily 3390.00, deduct 612.63, final 2438.37),
  `6279984722446` (3465.00, 627.08, 2490.92), `6279984722447`
  (3577.50, 644.50, 2575.00), and `6279984722448` (3667.50, 662.44,
  2638.06).  All are non-placeholder no-sales fallback rows, have zero price
  concession, keep ERP daily price as the calculation base, and satisfy
  `daily - official 10% - deduct = ERP final` exactly.
- Root cause is a real platform partial import, not readback parsing: the exact
  window activity is visibly marked `部分导入失败`, its canonical readback has no
  row for this item, and all other 384 rows are visible and exact.  The stale
  Taobao listing export still contains older SKU IDs, while the authoritative
  campaign mapping has used the current four IDs since 2026-08-07; the fixed
  correction binds only the current mapping and forbids SKU rotation.
- `POST /api/campaigns/correct-super-reduce-plan7-discount` and the controlled
  container CLI are fixed to workflow plan 7, snapshot 1, its raw artifact
  SHA-256 and the four-row canonical digest.  They first read the exact four
  rows.  If already exact, the call finishes without a write; if still missing,
  exactly one four-row single-item-discount workbook is submitted.  Any failed
  or unknown terminal is permanently claimed with no retry.  A successful
  terminal must be followed by a fresh exact four-row readback and an immutable
  evidence snapshot.  The route cannot call official campaign signup, change
  prices, rotate SKUs, touch the existing 384 rows, touch plan 8, withdraw,
  pause, delete, notify or automatically retry.
- Code/GitHub/production commit is
  `19e79784f6aea6e54ef5c96879621137a8623f96`.  Focused correction/audit/service
  identity tests passed 26/26; all 217 campaign tests passed except two
  intentional skips (215 passed, 2 skipped).  Python compilation, PowerShell
  parsing and diff checks passed.  The broader backend suite still has 27
  pre-existing failures in unrelated cost, inventory, supplier matching and
  order-detail tests; campaign tests have no failure.
- Unified NAS release passed health, ready, API/Web commit parity, migration
  `0144 (head)`, deployed route/CLI import and all API/Web/DB/backup containers.
  Rollback images are `panse-system-api:rollback-20260830-150408` and
  `panse-system-web:rollback-20260830-150721`.  The authoritative wrapper matches
  the released source (SHA-256
  `8A4D4B640B4776BDA796AE24F7A98F186D4E1E4AA24FB722628A8EE4DD3D625D`), and the
  production one-shot attempt is still absent: maintenance did not execute a
  readback or platform write.
- Task 01 may execute the single formal command once:
  `& 'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_correct_plan7_single_discount_nas.ps1'`.
  Its structured terminal plus post-submit readback is the only completion
  authority; a nonzero result must be returned to maintenance and must not be
  rerun.

## 2026-08-30 plan 7 single-discount evidence and terminal receipts

- The earlier one-item Super Reduce submission did not prove that the separate
  388-row SKU-level single-discount workbook was applied.  Plan 7 now has a
  fixed, service-authenticated read-only audit route and NAS wrapper.  It locks
  workflow `campaign:super-reduce:2026-09-01`, plan `7`, the canonical 388-row / 54-item
  scope digest, the exact update window, and the plan-only exclusion
  `805268708396`.
- The Web-Agent may only search the SKU-level single-discount list, open an exact
  activity's `修改优惠` readback and read visible SKU amounts/status.  It cannot
  select, fill, upload, submit, activate, pause, delete, notify or automatically
  retry.  Ambiguous activity IDs, missing rows and unknown states remain explicit
  differences instead of being called success.
- Migration `0144` adds append-only `campaign_evidence_snapshots`.  The audit
  stores the complete per-SKU classification plus the canonical raw readback
  artifact.  Future single-discount commits also persist the submitted target
  workbook, final platform counters, every failed row and the complete platform
  failure workbook in an independent transaction, so a later rollback cannot
  erase a partial-write receipt.
- A single-discount commit is now complete only when the platform terminal says
  `complete`, failed rows are zero and the successful row count exactly equals
  the requested row count.  Partial platform success fails closed and is never
  treated as full completion or an automatic retry signal.
- Code/production commit `0252a40276f6800f87a1099ea81b5c22d70154f0` passed
  94 focused ERP tests, campaign regression and Python compilation.  The unified
  NAS release passed `/api/health`, `/api/ready`, API/Web version parity,
  migration `0144 (head)` and all API/Web/DB/backup container checks.  No audit,
  signup, discount upload, submit, price change or platform account action was
  run during maintenance.
- Task 01 may run the only formal read-only audit command:
  `& 'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_audit_plan7_single_discount_nas.ps1'`.
  Its structured result, not the historical workbook alone, is the authority for
  deciding which of the 388 rows are correct, missing, mismatched or not effective.

## 2026-08-30 campaign plan-scope persistence incident

- The plan-7 platform submission was a one-item delta for item `797294092429`
  (two SKUs), not proof that every ERP product or every planned single-item
  discount had completed.  The latest read-only activity export contains 56
  item IDs and 496 SKU evidence rows, while the generated discount plan contains
  54 items and 388 rows; those are separate scopes and must not be collapsed
  into a single "all products completed" statement.
- Root cause: terminal platform classification replaced request-owned
  `official_all_store=true; official_exempt_items=805268708396` with a derived
  `official_active_items` marker.  That made later read-only rebuilds put the
  plan-scoped exempt bookcase back into signup and discount candidates.
- Terminal classification and qualification now preserve an operator-owned
  all-store scope and its explicit exemptions.  Explicit active-item plans keep
  their existing behavior.  Migration `0143` restores only workflow
  `campaign:super-reduce:2026-09-01`, removes its stale derived active marker,
  and restores item `805268708396` as a plan-only exemption.
- This repair performs no signup, upload, discount push, price change, retry,
  withdrawal, account action or notification.  Plan 7 remains incomplete until
  an audited coverage reconciliation separately proves existing official scope,
  the current one-item delta, and all required single-item-discount rows.
- Code/production commit `01ae63a84fd539659a070becce536e2b3e061708` passed
  203 campaign tests (201 passed, 2 intentional skips), Python compilation and
  diff checks.  The unified NAS release passed health/ready, API/Web version
  parity, migration `0143 (head)` and API/Web/DB/backup container checks.
  Rollback images: `panse-system-api:rollback-20260830-122920` and
  `panse-system-web:rollback-20260830-123341`.
- Production read-only rebuild after migration keeps plan 7 `alarmed`, produces
  exactly two signup rows for item `797294092429` and 388 discount rows across
  54 items, and excludes `805268708396` from both scopes.  This is scope-safety
  evidence only; it does not prove those 388 discount rows were applied.

## 2026-08-30 plan 7 submitted-price / paused-state verification

- The one approved plan-7 upload was submitted once and must never be replayed. Its terminal platform batch receipt was 1 item successful / 0 failed.
- Fresh export SHA256 `c2400fe896bc5f6da1e544faef099b7074a0d79e941aba9a7e9e58fbcd8ee88f` contains both reviewed SKU IDs with exact activity prices `1582.50` and `1410.00`, but the platform status is `暂停`. The old verifier discarded paused rows and incorrectly reported them as missing.
- `POST /api/campaigns/verify-super-reduce-plan7-post-submit` and `scripts/campaign_verify_super_reduce_plan7_post_submit_nas.ps1` are fixed to workflow `campaign:super-reduce:2026-09-01`, plan 7, attempt `dd0215218c70f952bb0865f8`, and the reviewed scope digest. They perform a fresh export and comparison only: no signup, upload, submit, price change, activation, notification, or retry.
- Exact-price paused rows now report `platform_imported_but_paused` and leave the plan alarmed. Only a later fresh export proving both exact SKU prices in `已发布设定`/`活动中` may reconcile ERP to `signup_pushed`; no platform action is performed by that reconciliation.

## 2026-08-29 Super Reduce plan 7 one-shot resume gate

- A dedicated audited route and container CLI now resume only
  `campaign:super-reduce:2026-09-01` / plan 7 from `alarmed`.  The local NAS
  wrapper has no campaign parameters and cannot select plan 8.
- The route locks workflow+plan, requires the exact reviewed two-SKU scope and
  price/final-price SHA-256, the exact plan-level exemption `805268708396`, and
  fresh plan-7 R16/R17 evidence.  It uses an explicit `resume_executing` CAS
  state and a durable one-shot receipt; a claimed, failed or unknown attempt is
  never automatically retried.
- The final preflight checks the actual two rows even when an older empty
  platform-qualified marker exists.  The recovery reuses the already persisted
  plan-scoped evidence and performs no duplicate pre-submit platform export.
  It preserves the mandatory post-submit exact export/SKU verification.
- This entry allows one Super Reduce signup upload only.  It cannot change
  prices, rotate SKUs, withdraw/pause/remove existing rows, touch plan 8, or
  auto-create a single-item discount after a no-sales result.
- Campaign regression, deployment commit, migration/head and production
  version/health evidence are recorded in the release handoff for this change.

## 2026-08-29 Super88 candidate price-evidence gate

- Formal `/api/campaigns/refresh-evidence` keeps the existing enrolled-item H/I
  export and, only for an exact fixed-window sign record, adds the Web-Agent's
  read-only not-yet-enrolled candidate evidence for the remaining ERP item/SKU
  allowlist.
- Candidate evidence stores `最低标价` and the platform's explicit
  `符合要求的建议活动价` as a distinct maximum eligible activity-price ceiling;
  it never fabricates a coupon-after line. R17 accepts this stricter platform
  ceiling only from the exact audited candidate source. If ERP daily/signup
  price exceeds it, the whole item is held without price mutation. Existing
  enrolled items still require the original fresh minimum-list and
  minimum-coupon-after H/I pair.
- Candidate `一口价` also refreshes R16 placeholder current-price evidence.
  Missing or ambiguous candidates remain hard-blocked. The same service-only
  refresh command remains read-only: no signup, upload, price change, account
  action, notification or automatic retry.
- Focused evidence/policy tests passed 33/33; the complete ERP campaign suite
  passed 187 tests with 2 intentional sample-dependent skips. Python compilation
  and diff checks passed.

## 2026-08-29 Feishu live QR handoff for finance login gates

- ERP code commit `70fb4d2` makes the existing live scan path explicit in the Feishu alert group. Finance/login alerts now tell the user to reply `@Panse System 扫码`; `发二维码`, `发送二维码`, `二维码`, `扫码登录`, `开始扫码` and `我要扫码` are accepted aliases.
- A reply starts the existing bounded pending-scan worker with `wait_scan=true`; the QR image is routed only to the configured Feishu alert group, never the order group, and remains live for at most 10 minutes. The scheduled finance run still does not open an unattended expiring QR session.
- The login alert and QR path keep finance amounts redacted. Successful scan, timeout and repeated copies of the same open login incident do not create duplicate notices.
- ERP verification: 53 focused tests and 124 alert/order/finance regression tests passed (2 intentional skips); Python compilation and diff checks passed. No live scan, login, bill pull, account action, finance replay or external test notification was performed.

## 2026-08-29 unsubmitted campaign plan resume/correction repair

- Code commit `6c09059d903b50290e7c2a0a638a2464d18fd3bf` repairs two bounded resume defects without weakening R16/R17 price-evidence gates or the one-shot submission boundary.
- A read-only activity-evidence refresh no longer replaces an operator-owned `official_all_store=true; official_exempt_items=...` scope with derived `official_active_items`. Formal `prepare` compares only request-owned scope/free text and ignores runtime evidence markers while preserving them on the plan; a real official-scope change still conflicts.
- The audited plan-scoped exemption correction accepts `alarmed` only when the plan has no submitted receipt, reconciliation report, or platform-write marker. Malformed receipt state fails closed. It can repair the exact legacy empty-active-scope drift with compare-and-swap semantics, including when the intended exemption list remains empty.
- This repair restores the safe correction path for workflows `campaign:super-reduce:2026-09-01` (plan 7) and `campaign:super88:49462:49469` (plan 8). It does not itself run correction, prepare, evidence refresh, signup, upload, price change, submission, account action, notification, or automatic retry.
- All campaign-named backend suites passed: 185 tests, 0 failures/errors, 2 intentional skips. Python compilation and diff checks also passed. No database migration is required; the schema head remains `0142`.

## 2026-08-28 audited prepare-only service identity for task 01

- GitHub `main` contains code commit `0d7de3bff2a63570f89eeea13f0703ebd322ab34` (`feat: add audited campaign prepare service identity`); production API and production Web are deployed from that code commit. The official unified NAS release passed health, ready, API/Web commit parity, required routes/features, database migration `0141 (head)` and API/Web/DB/backup container checks. Rollback images: `panse-system-api:rollback-20260828-021640` and `panse-system-web:rollback-20260828-022900`.
- Task 01 can invoke the formal preparation endpoint without a browser token, password or exported secret through `scripts/campaign_prepare_nas.ps1`. The wrapper accepts one local JSON file, sends it on stdin to the controlled production-container CLI, and the CLI calls the real `POST /api/campaigns/prepare` HTTP route. The exact command and request contract are in `docs/ERP活动正式后端入口_20260828.md`.
- Migration `0141` creates a dedicated encrypted `campaign_prepare_service_token`. It is not printed, returned or stored in the wrapper. Its machine identity is accepted only on the exact prepare path; it cannot list plans, change exclusions, upload, submit, retry, withdraw or call any other ERP API. Admin/operator Bearer access remains supported, while viewer and invalid credentials remain rejected.
- The controlled CLI uses the same global auth gate, endpoint dependency, Pydantic validation, durable `workflow_key` idempotency, campaign service and audit middleware as normal HTTP callers. It does not import a route handler, fabricate a `User`, write through ORM directly, read browser storage, or contact Taobao.
- A production-safe end-to-end probe submitted only `{\"workflow_key\":\"campaign:auth-probe\"}`. It reached the real endpoint and returned the expected HTTP 422 before handler/business writes because required campaign fields were absent. Audit row `32541` recorded `service:campaign-prepare`, `POST /api/campaigns/prepare`, status `422` and `authenticated path-scoped machine request`; the same identity received HTTP 401 on `/api/campaigns`. No plan, activity signup, upload, price change, account action, platform submission or external notification was created.
- New service-identity tests passed 4/4, the focused campaign/auth scope passed, and all campaign-named suites passed with 2 intentional skips. Python compilation, PowerShell/Bash syntax, Docker migration upgrade from `0140` to `0141`, encrypted-setting checks and release verification all passed.

## 2026-08-28 campaign internal preparation API and hard eligibility gates

- GitHub `main` contains code commit `2a67f6ad59e54a592ff7213336fa8e84f22f982d` (`fix: harden campaign internal preparation`); production API and production Web are deployed from that code commit. The unified NAS release passed health, ready, API/Web commit parity, migration `0140 (head)`, required routes/features and API/Web/DB/backup container checks. Rollback images: `panse-system-api:rollback-20260828-014516` and `panse-system-web:rollback-20260828-014921`.
- The formal ERP-internal entry is `POST /api/campaigns/prepare`. It is the only supported preparation path for ERP product mappings, daily/target prices, no-sales exclusions, durable campaign-plan identity, generated signup/discount rows and preflight. It never opens ERP pages and never submits to Taobao. The complete request contract and September examples are in `docs/ERP活动正式后端入口_20260828.md`.
- A stable `workflow_key` provides durable idempotency across process restarts: the same key and identity regenerate fresh rows/preflight on the same plan; a changed identity under the same key is rejected. `fixed_window` plans require exact title, both numeric platform IDs and exact start/end; `long_running_update` expresses a long-running Super Reduce activity with a bounded ERP update window plus `platform_active_until`, without inventing a short platform campaign.
- Historical no-sales registration is now a hard signup exclusion, not an advisory. Production migration/sanitization retained 34 valid registrations and removed the structurally invalid values; a post-release service read returned `invalid_registry_values=[]`. Invalid IDs such as `5`, `待定` and `暂无` cannot be added by grouping or feedback ingestion.
- Whole-item dedicated-link exclusion is auditable through `GET/POST /api/campaigns/item-exclusions` and `DELETE /api/campaigns/item-exclusions/{item_id}`. Automatic whole-item exclusion is allowed only when every authoritative mapped SKU is `is_custom_placeholder=true`; names and keywords are never used. Production verification classified all-placeholder item `1001358847694` as excluded while mixed normal-product items `792992319206`, `793128577437` and `793135173033` remained included. No unconfirmed candidate link was marked.
- Real-SKU signup price remains ERP daily price; final price remains the ERP mid/big campaign target. SKU rotation is disabled and any legacy rotation request is a hard preflight error. Existing active campaign items outside the new safe scope are preserved and reported; withdrawal, pause, cancellation and removal still require current explicit authorization for the exact item list.
- Structured official scope (`official_all_store`, `official_active_item_ids`, `official_exempt_item_ids`) is accepted directly by the preparation API. R15/R16/R17/R20 and fresh per-SKUID price-floor evidence remain hard gates; the preparation endpoint does not weaken or fabricate missing platform evidence.
- Focused campaign regression passed 137 tests; the broader campaign scope passed about 205 tests with 2 skipped. Python compilation, OpenAPI import, policy/image fingerprint and diff checks passed. The full backend suite still has 27 unrelated pre-existing failures in cost, inventory, supplier matching and legacy order import; representative failures reproduced on a clean reference worktree.
- The local Docker Desktop failure was recovered without a Windows reboot or factory reset by quarantining only stale zero-byte runtime socket directories and allowing Docker to recreate clean endpoints. Docker Engine 29.5.2 then responded twice and preserved the existing 21 containers and 96 images. This was host-runtime recovery only; no Docker data reset occurred.
- No activity signup, upload, price change, account/login action, withdrawal, business replay or external test notification was performed by task 02. Task 01 may now call the preparation API, but platform upload/submission and official receipts remain separate browser-controlled business steps.

## 2026-08-26 order notice and finance retry recovery

- Incident review: the 2026-08-25 order chain eventually completed at 20:37 after the matching shipping-report password arrived. The 20:00 health check had sent the ambiguous text “暂未进行订单更新” to the order group while the batch was still pending, so the message did not represent the final order result.
- The order group now receives a daily idempotent completion receipt only after a fresh successful order closure with zero new order images: “订单已完成更新，暂无新增需推送下单图”. Stale, failed, offline and password-pending states stay in the alert group and never masquerade as a completed order update.
- The 2026-08-25 20:30 finance run successfully refreshed Wanshifu, Wanshifu balance, Taobao aggregate balance, Wanxiangtai flow, enterprise Alipay balance and three enterprise Alipay bill days. Separate failures were promotion-balance OCR layout drift, a WeChat bill download timeout, one missing enterprise Alipay bill day, and expired personal-Alipay login.
- A personal-Alipay login gate no longer closes the whole finance pipeline. Promotion OCR, WeChat bill, enterprise Alipay gaps and other automatic failures retain their bounded 21:00/21:30/22:00 retries; the pipeline pauses only when every remaining failure truly needs user input.
- Enterprise Alipay daily recovery now retains the exact failed bill date and a bounded non-secret reason. Successful/non-login task runs clear stale scan-queue entries so an old login alert cannot pause a later automatic retry.
- The promotion balance reader supports both the old “万相台无界版·账户总余额” layout and the new “推广业务账户 → 万相台(元)” layout. It still rejects aggregate, deposit, withdrawable, frozen, direct-train and super-recommendation values. A read-only production OCR check on the 2026-08-25 screenshot returned high confidence, an explicit Wanxiangtai label and a numeric value; it did not write or expose the balance.
- ERP code commit/GitHub `main`/production API+Web commit: `c7389c9c2dffe85ce810ee24cb0da2858ae5b396`. Unified NAS verification passed health, ready, API/Web commit parity, migration `0139 (head)`, required routes/features and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260826-002820` and `panse-system-web:rollback-20260826-003556`.
- Scheduler restarted with 62 jobs; order pull, finance pull, both finance retry shifts and ingest health are enabled. Startup catch-up reported no missed same-day critical shift. The primary-PC Wake Bridge is enabled/running, the legacy Watchdog is disabled, and repeated post-release bridge polls returned HTTP 200 through the NAS.
- Focused ERP regression: 41 passed; Python compilation and diff checks passed. The full backend suite still has 28 previously existing failures in unrelated cost, inventory, pricing, supplier matching and legacy order-import areas. No business replay, bill pull, login, password submission or external test notification was performed by task 02.
- Remaining interaction gate: personal Alipay balance/flow still requires the user to re-login in the ERP automatic-data page. Its last persisted dates remain 2026-08-02 (balance) and 2026-08-01 (flow). The next normal finance shift at 20:30 will provide the first fresh production proof for the repaired automatic sources.

## 2026-08-25 Feishu order/alert group split

- GitHub `main` code commit and deployed production commit: `444613c8d28512037d2fad1ff316b131b404fa18`.
- Added an explicit notification route mode. `feishu_push_chat_id` remains the order group for order images, order delivery results and order-update notices; `feishu_alert_chat_id` is the separate target for automation errors, reports, password/login prompts, QR images, monthly inventory plans and remote-order reminder cards.
- Enabling `feishu_split` is fail-closed unless both order and alert groups are configured. Until then, `legacy` mode keeps the existing Enterprise WeChat webhook active so alerts are not silently lost. The webhook and inbound WeCom callback configuration are preserved as rollback paths.
- Production activation is complete. The existing `畔色系统群` remains the order group (masked suffix `921f5b`), `畔色ERP提醒` is bound as the alert group (masked suffix `732c4c`), and `notify_route_mode=feishu_split`; the targets are distinct.
- A production-code dry-run replaced the Feishu send function only inside an isolated diagnostic process: both `notify()` and `broadcast_text()` selected the alert-group suffix, while the configured order-group suffix remained unchanged. No external test message, order replay, password submission or business write was performed.
- Notification/order/reminder scope passed 30 focused tests; Python compilation and the frontend production build passed. The broader backend run still shows the previously documented unrelated cost, pricing, supplier-matching and legacy order-import failures; no new failure appeared in the changed scope.
- Unified NAS release passed at `444613c`: health, ready, API/Web commit parity, migration `0139 (head)`, required routes/features and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260825-215223` and `panse-system-web:rollback-20260825-215651`.
- The production scheduler restarted with 62 registered jobs and its delayed startup catch-up check reported no missed critical shift. No order replay, password submission, business write or notification send was used for this change.

## 2026-08-25 Taobao quota timeout self-healing and failure routing

- Incident evidence separates the layers. The 18:09 order run stopped on a single Taobao decryption-quota page `TimeoutError`; a later attempt on the same host reached the Agent and completed all three report downloads, with two reports imported and one new shipping report waiting for its matching password. This proves a transient execution failure plus a program defect that escalated the first timeout too early.
- Web-Agent GitHub `main` commit `65ea203ca65a1b97aeced37ce5a117fa19ecc2e4` adds three bounded quota-page reads and three bounded pre-submit browser-session attempts. DOM content is accepted even when `networkidle` times out. Export remains fail-closed when quota evidence is unavailable.
- A post-submit verification failure never retries the quota write: it records `submitted=true` / `verification_failed=true`, stops report export, and waits for review. This prevents a recovery loop from requesting quota twice.
- ERP GitHub `main` code commit and deployed production commit: `956d944bf271b11d5c1357945726f071f978c2eb`. Timeout, network, offline, rate-limit, login and password gates route to `execution` and retain the existing bounded schedule. Deterministic code/selector/schema errors route to `program_maintenance`, stop blind business retries, and enter a deduplicated, capped, structured maintenance queue that resolves on later success.
- Notification text now shows `处理归属`; the maintenance queue is available through the read-only `list_program_maintenance` service. No Tachikoma control-plane receipt is used because the user did not request one.
- ERP related regression passed 82 tests after excluding the already documented unrelated cross-midnight password-file test; the focused routing suite passed all 19 tests. Web-Agent focused quota/export tests passed 23 tests and its full suite passed 96 tests. Python compilation and diff checks passed in both programs.
- Unified NAS release passed: API/Web commit parity at `956d944`, health, ready, required routes/features, migration `0139 (head)`, and API/Web/DB/backup containers. Rollback images: `panse-system-api:rollback-20260825-195623` and `panse-system-web:rollback-20260825-200201`.
- The deployed API registered 62 scheduler jobs and its startup catch-up check reported no missed critical shift. On the primary Windows PC, `Panse-Web-Agent-Wake-Bridge` is enabled/running, the legacy always-on Watchdog remains disabled, and the Agent remains intentionally on-demand.
- Current business gate is separate from this repair: `ExportOrderList27021942261.xlsx` is waiting for a new matching shipping password. The old password was not retried blindly. No manual order replay, account action, quota write, password submission, or external test message was performed by maintenance task 02.

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
