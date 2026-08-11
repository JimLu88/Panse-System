"""支付宝流水智能核销 (plan §12.4 + 对账优化①)。

CSV 导入后 / 手动触发时跑一遍, 给 reconciliation_type 打标签。

三段匹配 (可靠度从高到低):
  1. 关联订单号: flow.related_order_no 命中
       - 收入 + 命中订单总表 order_no                 → customer_payment
       - 支出 + 命中工厂下单表 platform_order_no       → factory_payment
  2. 数据驱动对手方: 支出 + counterparty 命中库内真实工厂名 → factory_payment
  3. 关键字回退: 对手方/备注命中推广/物流/工资等关键字。

已有标的不动 (多半人工或上游标过)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import FactoryOrder, Order

_DIGITS = re.compile(r"\d{12,}")


def _order_key(raw: Optional[str]) -> Optional[str]:
    """从关联订单号里抽出可比对的订单号核心。

    支付宝里常带前缀和空格, 如 'T200P2701846635029001 070' → '2701846635029001070'。
    取去空格后最长的数字串 (>=12 位) 作为订单号。
    """
    if not raw:
        return None
    compact = re.sub(r"\s+", "", raw)
    runs = _DIGITS.findall(compact)
    if not runs:
        return None
    return max(runs, key=len)


PROMOTION_KEYS = ("推广", "淘宝商业", "现金消耗", "直通车", "万相台", "钻展")
LOGISTICS_KEYS = ("万师傅", "顺丰", "京东物流", "德邦", "圆通", "中通", "韵达", "申通",
                  "壹米滴答", "壹米", "安能物流")
SALARY_KEYS = ("工资", "薪资", "外包")
# 工厂名兜底关键字 (库里没工厂名时仍能粗匹配)
FACTORY_KEYS = ("家具", "工厂", "木业", "木器", "家居")

# ── 段 0: 用户写死规则 (2026-06-11 拍板, 永不再误归成采购/不再报异常) ──
# 理财申购/单次转入 = 支付宝余额⇄余额宝的账户内转移, 不是经营支出。
INTERNAL_TRANSFER_KEYS = (
    "理财申购", "理财赎回", "余额宝", "余利宝", "单次转入", "单次转出",
)
# 消费者体验提升计划服务费 = 淘宝官方按单代扣的平台服务费 (退货宝等, 按店铺类目/
# 规模/售后情况定价), 对手方多为「上海淘天商业管理有限公司」→ 平台费/经营。
PLATFORM_FEE_KEYS = (
    "消费者体验提升计划", "淘天商业", "平台服务费", "天猫服务费",
    # 2026-06-23: 支付宝企业号订单结算(T200P)的「软件服务费 / 消费券代付资金扣回」是平台扣款,
    # 此前因 amt<0 + 关联订单也有工厂单 → 误归 factory_payment(段1)。提到段0按平台费归类, 不再误当工厂货款。
    "软件服务费", "消费券",
)
# 消费者保证金充值 → 平台保证金 (资产, 非费用)
PLATFORM_DEPOSIT_KEYS = ("消费者保证金", "保证金充值")
# 退款 (2026-06-24, 治本; 同 line 59-61 平台费的修法): 交易退款/售后退款 = 退给买家的钱(amt<0),
# 挂在客户订单上。该客户订单常同时有工厂下单 → 若进段1按订单号命中工厂单会误判 factory_payment
# (实测 23 笔 -¥16536.59 工厂货款虚高)。故退款判定提到段0、早于段1。仅 amt<0 (退款支出);
# amt>0 的"退款"(退款撤销回款)留段1当客户回款。
REFUND_KEYS = ("交易退款", "售后退款", "退款", "退货")
# 售后补偿支出与退款不是同一财务口径，但都必须优先于「关联订单命中工厂单」。
# 实际支付宝备注为「售后支付-...」；旧规则漏掉它后会落入 factory_payment。
AFTERSALES_KEYS = ("售后支付", "售后赔付", "售后补偿")


def _matches(text: Optional[str], keys: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(k in text for k in keys)


@dataclass
class _Lookups:
    order_nos: set[str] = field(default_factory=set)
    factory_platform_nos: set[str] = field(default_factory=set)
    factory_names: tuple[str, ...] = ()


def _build_lookups(db: Session) -> _Lookups:
    order_nos = {
        k for (o,) in db.execute(select(Order.order_no).where(Order.order_no.isnot(None))).all()
        if (k := _order_key(o))
    }
    fac_nos = {
        k for (n,) in db.execute(
            select(FactoryOrder.platform_order_no).where(FactoryOrder.platform_order_no.isnot(None))
        ).all()
        if (k := _order_key(n))
    }
    fac_names = tuple(sorted({
        n.strip() for (n,) in db.execute(
            select(FactoryOrder.factory_name).where(FactoryOrder.factory_name.isnot(None))
        ).all() if n and n.strip()
    }, key=len, reverse=True))  # 长名优先, 避免短名误命中
    return _Lookups(order_nos=order_nos, factory_platform_nos=fac_nos, factory_names=fac_names)


@dataclass
class MatchResult:
    total_scanned: int
    tagged: dict[str, int]   # category -> count
    untouched: int


# 货款户(爱群号/主力号)是混合户(用户 2026-06-29确认): 也走零星配件采购。备注命中这些材料词的支出
# 不当博冠货款, 留作未归类 → 由配件采购归账(create_purchases→actual_parts)。与"X月货款/博冠"区分。
_PARTS_REMARK_KEYS = ("岩板", "玻璃", "榉木", "木材", "大板", "贴皮", "木皮", "洞石",
                      "轨道", "灯带", "五金", "配件", "软包", "螺丝", "双面胶",
                      "把手", "铰链", "拉手", "台面", "桌面", "板材")


_PLAT_ORDER_RE = re.compile(r"\d{15,19}")


def _is_parts_flow(flow: AlipayFlow) -> bool:
    """货款户里这笔支出是否为"零星配件采购"(而非博冠货款): 备注命中材料词, 或带淘宝平台订单号
    (按订单的配件采购, 货款不会带平台订单号)。两条任一命中即判配件。"""
    if any(k in (flow.remark or "") for k in _PARTS_REMARK_KEYS):
        return True
    if _PLAT_ORDER_RE.search(flow.platform_order_no or ""):
        return True
    return False


def _classify(flow: AlipayFlow, lk: _Lookups, db: Optional[Session] = None) -> Optional[str]:
    if flow.reconciliation_type:  # 已经有标了, 不动
        return None
    amt = float(flow.amount or 0)
    has_ron = bool((flow.related_order_no or "").strip())
    ron = _order_key(flow.related_order_no)
    desc = " ".join(filter(None, [flow.counterparty, flow.remark, flow.transaction_type]))

    # 闸①(用户 2026-06-29, #1+#2): 只对【博冠货款户(爱群号/主力号)】的流水按账户角色判, 不再被对方名/
    # 关键字误打 factory_payment(根治"伟男/博冠货款被当工厂对账 + 僵尸复发")。仅在传 db 时生效, 不传退回旧行为。
    # 作用域限定 boguan 账户: 不动其它账户的对方名/关键字分类(避免误改 salary/promotion 等)。
    if db is not None:
        from app.services import account_registry_service as _reg
        if _reg.is_boguan_account(db, flow.account):
            from app.services import internal_accounts as _ia
            if _ia.is_internal_flow(db, flow):
                return "internal_transfer"      # 货款户内部挪钱(对方是我方账户主人/内部人员) → 只对账不记账
            # 混合户: 支出且备注是配件材料(岩板/玻璃/榉木/轨道…) → 不当博冠货款, 留未归类供配件采购归账
            # (用户 2026-06-29: 爱群号既有博冠货款也有零星配件采购)。货款("X月货款"/博冠)无材料词 → 仍博冠货款。
            if amt < 0 and _is_parts_flow(flow):
                return None
            return "boguan_payment"             # 货款户其余流水 → 博冠货款(走 #8 专用对账, 不进通用工厂对账)

    # 段 0: 用户写死的确定性规则, 优先于一切 (理财转移/平台代扣费/保证金)
    if _matches(desc, INTERNAL_TRANSFER_KEYS):
        return "internal_transfer"
    if _matches(desc, PLATFORM_DEPOSIT_KEYS):
        return "platform_deposit"
    if _matches(desc, PLATFORM_FEE_KEYS):
        return "platform_fee"
    # 客户售后/退款优先(早于段1): 支出挂客户订单, 而该单通常也有工厂单，若先按订单号匹配
    # 就会把「售后支付」误判成 factory_payment。
    if amt < 0:
        if _matches(desc, AFTERSALES_KEYS):
            return "aftersales"
        if _matches(desc, REFUND_KEYS):
            return "refund"

    # 段 1: 关联订单号直挂 (归一化后比对真实订单/工厂下单)
    if ron:
        if amt > 0 and ron in lk.order_nos:
            return "customer_payment"
        if amt < 0 and ron in lk.factory_platform_nos:
            return "factory_payment"

    if amt < 0:
        # 段 2: 数据驱动 — 对手方命中库内真实工厂名
        cp = (flow.counterparty or "").strip()
        if cp and any(name and (name in cp or cp in name) for name in lk.factory_names):
            return "factory_payment"
        # 段 3: 关键字回退
        if _matches(desc, FACTORY_KEYS):
            return "factory_payment"
        if _matches(desc, PROMOTION_KEYS):
            return "promotion"
        if _matches(desc, LOGISTICS_KEYS):
            return "logistics"
        if _matches(desc, SALARY_KEYS):
            return "salary"
    elif amt > 0 and has_ron:
        # 收入且带关联订单号 → 客户回款 (兜底, 即使订单未导入)
        return "customer_payment"
    return None


def reclassify_refund_mislabels(db: Session, *, account: Optional[str] = None) -> dict:
    """纠正存量客户退款/售后支出误分类，并保留旧函数名兼容已有调用。

    根因同 _classify 的客户支出优先修复: 此前段1按订单号命中工厂单，把退款或「售后支付」
    误判成 factory_payment。本函数把已有错误标签纠正为 refund / aftersales；空分类仍交给正常流程。
    幂等: 已是目标分类的不动。返回 {原type: {'count', 'sum'}} 明细供核对。
    """
    from app.services import field_change_service as _fcs
    # 空分类留给下方正常分类流程处理；这里仅修复已经被机器写错的历史标签。
    stmt = select(AlipayFlow).where(
        AlipayFlow.amount < 0,
        AlipayFlow.reconciliation_type.isnot(None),
    )
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    # 人工锁 (2026-07-12): 人改过核销类型的流水不许机器再翻 (对称于 route 的锁, 见 human_pks)
    _locked = _fcs.human_pks(db, table="alipay_flows", field="reconciliation_type")
    detail: dict[str, dict] = {}
    for f in rows:
        if str(f.id) in _locked:
            continue
        desc = " ".join(filter(None, [f.counterparty, f.remark, f.transaction_type]))
        target = None
        if _matches(desc, AFTERSALES_KEYS):
            target = "aftersales"
        elif _matches(desc, REFUND_KEYS):
            target = "refund"
        if target is None or (f.reconciliation_type or "") == target:
            continue
        old = f.reconciliation_type or "(未分类)"
        d = detail.setdefault(old, {"count": 0, "sum": 0.0})
        d["count"] += 1
        d["sum"] += float(f.amount or 0)
        f.reconciliation_type = target
    db.flush()
    return detail


def run(db: Session, *, account: Optional[str] = None) -> MatchResult:
    """扫所有 reconciliation_type 为空的流水, 自动打标."""
    # 每次自动分类前先修复历史误分类，避免旧的 factory_payment 标签永久绕过空值扫描。
    reclassify_refund_mislabels(db, account=account)
    lk = _build_lookups(db)
    stmt = select(AlipayFlow).where(AlipayFlow.reconciliation_type.is_(None))
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    tagged: dict[str, int] = {}
    for r in rows:
        category = _classify(r, lk, db)
        if category:
            r.reconciliation_type = category
            tagged[category] = tagged.get(category, 0) + 1
    db.flush()
    return MatchResult(
        total_scanned=len(rows),
        tagged=tagged,
        untouched=len(rows) - sum(tagged.values()),
    )
