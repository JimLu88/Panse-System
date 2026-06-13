"""内部账户/员工代付识别 (用户口径, 写死进系统).

我方 4 个支付宝账户: 企业号(义乌市畔色) / 佳宝号 / 爱群号 / 主力号。
账户之间互转、以及转给内部人员(魏佳英/魏佳音/爱群…)的钱不是经营支出:
  - 不生成配件采购记录 (alipay_flow_router_service)
  - 不进供应商候选 (suppliers API 两端)
  - 已误生成的采购记录由 purge 清理 (每次流水归类时自动跑)

例外: 员工代购 — 对手方是内部人员、但备注是真实货品(如"榉木床头柜钢板定制"),
是员工用私人淘宝号代付的真实采购, 保留为采购记录并标 purchase_type=员工代购,
公司转账即该笔支出本身(只计一次), 但内部人员永远不当供应商档案。
"""
from __future__ import annotations

# 档位一: 已导入账户的主人 (企业号=畔色 / 爱群号=爱群 / 主力号·佳宝号相关=魏佳音)
# 给他们的任何打款都是内部挪钱 — 真实支出会出现在他们自己账户的导入流水里, 重复计就是双计。
IMPORTED_ACCOUNT_OWNER_KW = (
    "魏佳音", "爱群", "Klossy", "畔色", "**群", "**音",
)

# 档位二: 员工代付 (魏佳英用私人淘宝号买配件, 其个人流水不会被导入)
# 转账理财类 → 内部互转; 带真实货品备注 → 员工代购采购(公司转账即该笔支出, 只计一次)。
EMPLOYEE_PROXY_KW = (
    "魏佳英", "佳英", "**英",
)

# 内部人员/自营主体关键词 (供应商候选统一排除; 含支付宝掩码形态)
INTERNAL_COUNTERPARTY_KW = IMPORTED_ACCOUNT_OWNER_KW + EMPLOYEE_PROXY_KW

# 转账/理财类字样 → 资金在自己口袋间挪动, 不是买东西
TRANSFER_LIKE_KW = (
    "转入", "转出", "转账", "理财", "申购", "赎回", "提现", "余额宝", "单次转",
)

EMPLOYEE_PROXY_PURCHASE_TYPE = "员工代购"


def is_internal_counterparty(name: str | None) -> bool:
    """对手方是否我方内部人员/主体 (供应商候选与采购生成都要排除)。"""
    if not name:
        return False
    return any(k in name for k in INTERNAL_COUNTERPARTY_KW)


def is_transfer_like(*texts: str | None) -> bool:
    """备注/类型是否转账理财类字样。"""
    blob = "".join(t for t in texts if t)
    return any(k in blob for k in TRANSFER_LIKE_KW)


def is_imported_account_owner(name: str | None) -> bool:
    """对手方是否已导入账户的主人 (给他们打款 = 一律内部互转)。"""
    if not name:
        return False
    return any(k in name for k in IMPORTED_ACCOUNT_OWNER_KW)


def is_internal_transfer(counterparty: str | None, *texts: str | None) -> bool:
    """内部互转判定 (写死的用户口径):

    - 已导入账户主人 → 一律互转 (其真实支出由自己账户流水捕获, 再记就是双计)
    - 员工代付人员 → 转账理财字样或无业务备注才算互转;
      带真实货品备注 = 员工代购, 不算互转 (调用方自行标 purchase_type)。
    """
    if is_imported_account_owner(counterparty):
        return True
    if not is_internal_counterparty(counterparty):
        return False
    blob = "".join(t for t in texts if t).strip()
    return (not blob) or is_transfer_like(blob)
