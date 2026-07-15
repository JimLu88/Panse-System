# Web-Agent 任务规格: `tax_information` 涉税信息报送抓取

> 用户拍板 2026-07-14: 税费口径从「按下单时间估算」纠正为「税务局打款口径」,
> 唯一真源 = 千牛 财务→收支账单→**涉税信息报送账单**。
> ERP 侧已全部就绪(tax_report_service + cash_flow 叠加 + 周日19:00 调度),
> **农场端(192.168.31.91 Panse-Web-Agent)按本规格加任务后即全自动**,ERP 无需再改。

## 任务定义

- **id**: `tax_information`
- **title**: 导入涉税报送收入(税费打款口径)
- **cadence**: weekly(ERP 每周日 19:00 触发;幂等,同季覆盖)
- **login_key**: `taobao`(复用千牛登录态)
- **task_url**: `https://myseller.taobao.com/home.htm/whale-accountant/bill/summary?billDirection=TaxInformation&billType=month`
- **inputs(variables)**: `{"year": 2026, "quarters": [1,2,3], "entity": "义乌市畔色贸易商行(个体工商户)"}`

## 页面操作步骤(2026-07-14 实操核对过的页面结构)

对 `quarters` 里每个季度 Q 依次执行:

1. 点「报送年度」输入框 → 年份网格选择器弹出(2020–2029)→ 点 `year`(如 2026);
2. 点「报送季度」输入框 → **季度区间选择器**弹出(开始季度/结束季度两段)→ 点 `QN` 一次(设开始)再点同一个 `QN`(设结束)——单季即 起=止;
3. 点「主体」下拉 → 选项约 3 个(旧身份证号 33012119…、魏国荣、**义乌市畔色贸易商行(个体工商户)**)→ 选与 `entity` 匹配的一项;
   - 注意:2026-Q1 若该主体查询为空,回退再查旧主体 330121196309095118(Q1 历史上可能挂旧主体),取有数据的一边;Q2 起只认 `entity`。
4. 点「搜索」;
5. 读「收入信息报送表」的结果行:`报送周期`(如 2026第1季度 01-03月)、**收入净额**、收入总额、退款金额、销售货物、支付给平台的佣金服务费合计金额;
   - 表若显示「亲,您当前的经营主体信息不在本轮涉税报送范围」= 该季该主体无数据,记 null 跳过。

## 输出契约(ERP 解析依赖,勿改字段名)

job 结果(`GET /api/jobs/{jid}` 的 result)必须包含:

```json
{
  "quarters": {
    "2026-Q1": {"net_income": 491255.80, "gross": 495441.49, "refund": 4185.69},
    "2026-Q2": {"net_income": 0.0}
  }
}
```

- 键 = `"{year}-Q{n}"`;`net_income` 必填(数值),`gross`/`refund` 可选;
- 无数据的季度**不要**放进 quarters(或整键省略),不要放 0 假数;
- ERP 侧 `tax_report_service.ingest` 落库 `system_settings[tax_report_quarters]`,
  现金流税费对已报送季度自动改用 `net_income × 2%`(basis=报送),当季未报送仍走订单估算。

## 验证方式

农场端加好任务后,ERP 容器里跑:

```python
from app.database import SessionLocal
from app.services import tax_report_service
db = SessionLocal(); print(tax_report_service.pull_via_agent(db))
```

预期 `{"ok": True, "ingested": ["2026-Q1", ...]}`;之后 剩余流水→编辑手动项 的季度税额
即变为报送口径(Q1 应显示 9,825.12 = 491,255.80×2%,而非旧估算 7,820.83)。
