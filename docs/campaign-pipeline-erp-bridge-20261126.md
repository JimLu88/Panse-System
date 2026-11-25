# 第五轮活动程序 ERP 接入（2026-11-26）

## 本次接入

ERP 活动向导新增「新活动程序 · ERP 对接」，可读取本场实时数据和原任务保护状态。旧「交接初版核对器」保留为历史比较，不代表第五轮程序。

本机统一入口 `scripts/campaign_pipeline_erp_runner.py` 自动从 ERP 读取价格、可报 SKU、固定定制基线及精确活动窗口，不需要用户导出或上传 JSON。运行时校验第五轮 calc、pipeline、CLI 和浏览器路由源码指纹。没有现成操作员 PANSE_API_TOKEN 时明确报错，不生成令牌。

```powershell
# 只读数据及本地合同验证
python scripts/campaign_pipeline_erp_runner.py --plan-id <计划ID>
# 使用原程序准备文件；不执行报名
python scripts/campaign_pipeline_erp_runner.py --plan-id <计划ID> --prepare
# 正式执行仅用于当次获准新活动，且服务端开关已开启
python scripts/campaign_pipeline_erp_runner.py --plan-id <计划ID> --execute
# 断线后只读原本地任务状态并补回执，不上传、不重新启动
python scripts/campaign_pipeline_erp_runner.py --plan-id <计划ID> --recover <原ERP任务ID>
```

默认 API 为 NAS 8200；本机服务为现有专用 Edge 8502。入口使用现有 CAMPAIGN_EDGE_TOKEN 或原 CLI 凭据文件；凭据不写入输出。源码一致不等于正在运行的守护进程已加载该版本，正式验收必须核对服务版本及店铺。

## 防重复与恢复

真实提交前在 CampaignExecutionAttempt 持久占位；同一官方活动（包括旧URL中的身份）共用 PostgreSQL 事务锁。新报名入口与旧报名 claim 互相保护。只有没有旧占位的新草稿可进入新执行入口；旧报警、已核对计划保持原流程。

每个 ERP 占位使用独立本地目录。超时保存 unknown_pending_verify，原占位不释放。--recover 只读取原目录状态，追加回执历史；明确终态不能覆盖。ERP 回执保存失败可以再次收取原结果，不重新报名。若占位请求本身超时，先在 ERP 查原占位，不能猜造任务编号。

## 已知范围与启用条件

- `PANSE_CAMPAIGN_PIPELINE_LIVE_ENABLED` 默认关闭。本次发布不打开，不提交淘宝。
- 仅固定档期，长期活动和商品/SKU级立减绑定继续原入口。禁止把某件商品的立减ID当作全店活动ID。
- 缺首次定制基线会停下；只读接口不偷偷补建基线。
- ERP 原单品立减上传入口也登记旧程序所有权，并拒绝新程序占位后的上传；其原任务重试规则不改。直接绕开 ERP 的独立程序不在本接口覆盖范围，不可与新入口同时执行。
- 本地 done 只作为适配器证据，不直接修改计划为 reconciled；仍需官方完整清单及 ERP 逐SKU核对。
- 还需当次获准新活动完成真实平台验收。离线测试、数据接口和页面部署不等于报名成功。

## 核验

候选服务在 NAS 数据库 READ ONLY 事务中实测。旧报名计算会初始化基线的副作用已通过独立 seed_baselines=False 路径消除；原入口默认行为保持。

原计划8读取231条SKU且保留9条原占位；其他历史计划按缺身份、立减绑定或旧状态分别保护。生产核验与测试记录位于 `D:/AI/畔色ERP系统/outputs/campaign-pipeline-integration-20261125`。API、Web发布版本以部署验收回执为准。
