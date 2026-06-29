# -*- coding: utf-8 -*-
"""账户角色注册表 (#1, 用户 2026-06-29): 把"按对方名字猜账户性质"换成"按账户角色"判定。

每个支付宝账户有一组角色(可叠加), 存在 system_settings['account_roles'] 的 JSON 配置里
(复用 factory_aliases 同款 settings_service 读写 + finance.py GET/PUT 留痕)。配置缺失时回落到
种子默认 —— 种子严格等价现状(non_revenue=爱群/佳宝/主力, ledger_exempt=爱群/佳宝), 首次上线零行为变化。

角色枚举:
- revenue        : 客户回款/经营户(企业号 / 个体户私账)。营收对账/营收大盘只认这类正流水。
- boguan_payment : 博冠货款户 = 爱群号 + 主力号(用户 2026-06-29: 博冠的账只走李爱群+主账号两个账户)。
- internal       : 内部户(佳宝号 / 个体户私账)。内部账号间流转「只对账不记账」。
- ledger_exempt  : 总账勾稽豁免(流水不完整/不连续, 只查账面自洽)= 爱群号 + 佳宝号。

派生口径(供各对账/记账处取数, 替代散落的硬编码列表):
- non_revenue_accounts      = 不含 revenue 角色的已登记账户 = (爱群号, 佳宝号, 主力号)  [= 旧 _NON_REVENUE_ACCOUNTS]
- ledger_flow_exempt_accounts = 含 ledger_exempt 角色 = (爱群号, 佳宝号)               [= 旧 _LEDGER_FLOW_EXEMPT]
- boguan_accounts           = 含 boguan_payment 角色 = (爱群号, 主力号)               [#8 博冠专用对账取数范围]
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

_SETTINGS_KEY = "account_roles"

# 种子默认: 配置为空时使用, 严格等价现状(改了会变现有对账结果, 务必保持)。
_SEED_ACCOUNT_ROLES: dict[str, list[str]] = {
    "企业号": ["revenue"],
    "个体户私账": ["revenue", "internal"],
    "爱群号": ["boguan_payment", "ledger_exempt"],
    "佳宝号": ["internal", "ledger_exempt"],
    "主力号": ["boguan_payment"],
}

# 模块级缓存(随 settings 写入 invalidate); 避免 smart_matching 扫全表时 N 次查 system_settings。
_cache: Optional[dict[str, list[str]]] = None


def invalidate() -> None:
    """配置写入后清缓存(PUT 端点里调)。"""
    global _cache
    _cache = None


def _load_roles(db: Session) -> dict[str, list[str]]:
    """读 system_settings['account_roles'] JSON; 容错 + 缺失回落种子默认。仿 _factory_aliases。"""
    global _cache
    if _cache is not None:
        return _cache
    roles: dict[str, list[str]] = dict(_SEED_ACCOUNT_ROLES)
    try:
        from app.services import settings_service
        raw = settings_service.get(db, _SETTINGS_KEY, env_fallback=False)  # env_fallback=False: 同 _factory_aliases
        if raw:
            m = json.loads(raw)
            if isinstance(m, dict) and m:
                roles = {str(k).strip(): [str(r).strip() for r in (v or []) if str(r).strip()]
                         for k, v in m.items() if str(k).strip()}
    except Exception:  # pragma: no cover - 配置坏不拦对账, 回落种子
        roles = dict(_SEED_ACCOUNT_ROLES)
    _cache = roles
    return roles


def roles_of(db: Session, account: Optional[str]) -> set[str]:
    """账户的角色集合(未登记账户 → 空集 = 默认按经营/营收处理, 与现状一致)。"""
    if not account:
        return set()
    return set(_load_roles(db).get(account.strip(), []))


def has_role(db: Session, account: Optional[str], role: str) -> bool:
    return role in roles_of(db, account)


def _accounts_with(db: Session, role: str) -> tuple[str, ...]:
    return tuple(a for a, rs in _load_roles(db).items() if role in rs)


def is_revenue_account(db: Session, account: Optional[str]) -> bool:
    return has_role(db, account, "revenue")


def is_boguan_account(db: Session, account: Optional[str]) -> bool:
    return has_role(db, account, "boguan_payment")


def is_internal_account(db: Session, account: Optional[str]) -> bool:
    return has_role(db, account, "internal")


def non_revenue_accounts(db: Session) -> tuple[str, ...]:
    """不含 revenue 角色的已登记账户 = 旧 _NON_REVENUE_ACCOUNTS(爱群号/佳宝号/主力号)。
    营收对账/营收大盘/工厂货款对账都排除这些(货款/采购/对外付款户, 非客户回款)。"""
    return tuple(a for a, rs in _load_roles(db).items() if "revenue" not in rs)


def ledger_flow_exempt_accounts(db: Session) -> tuple[str, ...]:
    """含 ledger_exempt 角色 = 旧 _LEDGER_FLOW_EXEMPT(爱群号/佳宝号), 总账只查账面自洽不做流水勾稽。"""
    return _accounts_with(db, "ledger_exempt")


def boguan_accounts(db: Session) -> tuple[str, ...]:
    """博冠货款户(爱群号 + 主力号), 供 #8 博冠按月货款专用对账取数。"""
    return _accounts_with(db, "boguan_payment")


def all_known_accounts(db: Session) -> tuple[str, ...]:
    """已登记角色的账户清单(供 UI 列表)。"""
    return tuple(_load_roles(db).keys())
