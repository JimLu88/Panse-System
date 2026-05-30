"""支付宝流水 → 订单 反向匹配回填 (自己找规律).

背景: 历史上几个支付宝号混用, 关联订单号字段格式很乱:
  - 9a 企业号:  'T200P2701846635029001 070'  (T200P 前缀 + 订单号 + 空格分片)
  - 备注列:     '基础软件服务费(2701846635029001070)扣款'
  - 9c 爱群号:  平台订单号列直接是 19 位订单号
  - 9d 佳宝号:  '202604232000400111006 80090321541' (理财, 非订单)

目标: 不写死单一格式, 而是从订单总表已知订单号出发, 自动学习"从一条流水里
怎么抠出订单号"的规律, 再把订单号 → 流水号回填到订单上 (Order.alipay_flow_no)。

核心思路 (data-driven, 自己找规律):
  1. 把订单总表所有 order_no 建成集合, 同时按"尾部 N 位"建倒排索引 (订单号常被分片/截断)。
  2. 对每条流水, 从 related_order_no + remark + counterparty_account 等文本里
     用多个候选抽取器掏出数字串, 逐个比对:
       a. 整串命中订单号
       b. 去前缀 (T200P / 纯字母前缀) 后命中
       c. 作为某订单号的子串 / 订单号作为它的子串 (应对分片空格、尾号截断)
  3. 命中唯一订单 → 记一条 (order_no, flow_no, 规律标签, 置信度)。
  4. 命中多个订单 → 标 ambiguous, 不回填 (交人工)。

只读分析用 analyze(); 落库用 backfill()。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import Order

# 订单号核心: 连续数字串. 淘宝订单号一般 16-20 位。
_RUN = re.compile(r"\d{6,}")
# 已知会出现的字母前缀 (流水里订单号常被加前缀)
_KNOWN_PREFIXES = ("T200P", "TT", "P", "LC", "HJCAEB")
# 比对时取订单号尾部多少位做倒排键 (应对分片/截断, 尾号最稳定)
_TAIL = 12


def _digit_runs(text: Optional[str]) -> list[str]:
    """从一段文本里掏出所有较长数字串 (去掉串内空格再找)。"""
    if not text:
        return []
    compact = re.sub(r"\s+", "", str(text))
    return _RUN.findall(compact)


def _strip_prefix(token: str) -> str:
    """去掉已知字母前缀残留 (数字串里一般已无字母, 这里兜底处理混合串)。"""
    for p in _KNOWN_PREFIXES:
        if token.startswith(p):
            return token[len(p):]
    return token


@dataclass
class _OrderIndex:
    exact: set[str] = field(default_factory=set)              # 全部订单号
    by_tail: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))  # 尾12位 -> [order_no]

    def add(self, order_no: str) -> None:
        self.exact.add(order_no)
        if len(order_no) >= _TAIL:
            self.by_tail[order_no[-_TAIL:]].append(order_no)


def _build_order_index(db: Session) -> _OrderIndex:
    idx = _OrderIndex()
    for (ono,) in db.execute(
        select(Order.order_no).where(Order.order_no.isnot(None))
    ).all():
        s = str(ono).strip()
        if s:
            idx.add(s)
    return idx


@dataclass
class Hit:
    order_no: str
    rule: str          # 命中规律标签 (exact / strip_prefix / tail_index / substring)
    confidence: float  # 0~1


def _match_token(token: str, idx: _OrderIndex) -> Optional[Hit]:
    """单个数字串 → 订单号. 从最可靠规律往下试, 命中唯一才算。"""
    if not token or len(token) < 6:
        return None
    # a. 整串精确命中
    if token in idx.exact:
        return Hit(token, "exact", 1.0)
    # b. 去字母前缀后命中
    stripped = _strip_prefix(token)
    if stripped != token and stripped in idx.exact:
        return Hit(stripped, "strip_prefix", 0.97)
    # c. 尾部 N 位倒排: token 尾段命中某订单号尾段 (应对分片空格/前缀脏数据)
    if len(token) >= _TAIL:
        cands = idx.by_tail.get(token[-_TAIL:])
        if cands and len(set(cands)) == 1:
            cand = cands[0]
            # token 必须是订单号的子串, 或反之 (防尾号巧合)
            if cand in token or token in cand or token[-_TAIL:] == cand[-_TAIL:]:
                return Hit(cand, "tail_index", 0.9)
    return None


def _candidates_from_flow(flow: AlipayFlow) -> list[str]:
    """一条流水里所有可能含订单号的数字串 (来源: 关联订单号 > 备注 > 对手账户)。"""
    seen: list[str] = []
    for src in (flow.related_order_no, flow.remark, flow.counterparty_account):
        for run in _digit_runs(src):
            if run not in seen:
                seen.append(run)
    return seen


def _resolve_flow(flow: AlipayFlow, idx: _OrderIndex) -> tuple[Optional[Hit], bool]:
    """解析一条流水 → (命中, 是否歧义)。歧义=掏出的串命中了多个不同订单。"""
    hits: dict[str, Hit] = {}
    for tok in _candidates_from_flow(flow):
        h = _match_token(tok, idx)
        if h:
            # 同订单多次命中保留最高置信度
            if h.order_no not in hits or h.confidence > hits[h.order_no].confidence:
                hits[h.order_no] = h
    if not hits:
        return None, False
    if len(hits) > 1:
        return None, True  # 命中多个订单, 歧义
    only = next(iter(hits.values()))
    return only, False


@dataclass
class BackfillResult:
    total_flows: int                       # 扫描的流水数 (有候选订单号的)
    matched_orders: int                    # 成功回填的订单数
    filled_flow_no: int                    # 写入 Order.alipay_flow_no 的条数
    ambiguous: int                         # 歧义流水数 (命中多单, 跳过)
    unmatched: int                         # 有数字串但匹配不到订单
    by_rule: dict[str, int] = field(default_factory=dict)   # 各规律命中数
    samples: list[dict] = field(default_factory=list)       # 命中样本 (前若干条)


def _scan(db: Session, *, account: Optional[str] = None) -> tuple[dict[str, tuple[AlipayFlow, Hit]], BackfillResult]:
    """扫流水, 返回 {order_no: (flow, hit)} 与统计. 收入流水优先 (客户回款最该带订单号)。"""
    idx = _build_order_index(db)
    stmt = select(AlipayFlow)
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    # 收入 (amount>0) 排前面: 同一订单若多条流水命中, 优先认客户回款那条
    flows = db.execute(stmt).scalars().all()
    flows.sort(key=lambda f: float(f.amount or 0), reverse=True)

    chosen: dict[str, tuple[AlipayFlow, Hit]] = {}
    by_rule: dict[str, int] = {}
    total = ambiguous = unmatched = 0
    for f in flows:
        cands = _candidates_from_flow(f)
        if not cands:
            continue
        total += 1
        hit, is_ambi = _resolve_flow(f, idx)
        if is_ambi:
            ambiguous += 1
            continue
        if not hit:
            unmatched += 1
            continue
        by_rule[hit.rule] = by_rule.get(hit.rule, 0) + 1
        # 一个订单只认第一条 (已按金额排序, 收入优先)
        chosen.setdefault(hit.order_no, (f, hit))

    result = BackfillResult(
        total_flows=total,
        matched_orders=len(chosen),
        filled_flow_no=0,
        ambiguous=ambiguous,
        unmatched=unmatched,
        by_rule=by_rule,
    )
    return chosen, result


def analyze(db: Session, *, account: Optional[str] = None, sample_limit: int = 20) -> BackfillResult:
    """只读: 看能匹配多少, 命中了哪些规律, 给样本. 不写库。"""
    chosen, result = _scan(db, account=account)
    for ono, (flow, hit) in list(chosen.items())[:sample_limit]:
        result.samples.append({
            "order_no": ono,
            "flow_no": flow.transaction_no,
            "account": flow.account,
            "rule": hit.rule,
            "confidence": hit.confidence,
            "related_order_no": flow.related_order_no,
        })
    return result


def backfill(
    db: Session, *, account: Optional[str] = None, only_missing: bool = True,
) -> BackfillResult:
    """落库: 把命中的流水号写回 Order.alipay_flow_no。

    - only_missing: 仅回填 alipay_flow_no 为空的订单
    Returns: BackfillResult (filled_flow_no = 实际写入条数)
    """
    chosen, result = _scan(db, account=account)
    filled = 0
    samples_kept = 0
    for ono, (flow, hit) in chosen.items():
        order = db.execute(
            select(Order).where(Order.order_no == ono)
        ).scalar_one_or_none()
        if order is None:
            continue
        if only_missing and order.alipay_flow_no:
            continue
        order.alipay_flow_no = flow.transaction_no
        filled += 1
        if samples_kept < 20:
            result.samples.append({
                "order_no": ono,
                "flow_no": flow.transaction_no,
                "account": flow.account,
                "rule": hit.rule,
                "confidence": hit.confidence,
            })
            samples_kept += 1
    db.flush()
    result.filled_flow_no = filled
    return result
