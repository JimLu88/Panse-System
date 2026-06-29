# -*- coding: utf-8 -*-
"""账户角色注册表 #1 (用户 2026-06-29): 种子默认必须严格等价现状 + 配置可覆盖。"""
from app.services import account_registry_service as reg
from app.services import settings_service
from app.services.reconciliation_service import _NON_REVENUE_ACCOUNTS, _LEDGER_FLOW_EXEMPT


def test_seed_equivalent_to_current_hardcoded(db_session):
    """配置缺失时, 派生口径必须正好等于现有硬编码常量(首次上线零行为变化)。"""
    reg.invalidate()
    assert set(reg.non_revenue_accounts(db_session)) == set(_NON_REVENUE_ACCOUNTS)        # (爱群号,佳宝号,主力号)
    assert set(reg.ledger_flow_exempt_accounts(db_session)) == set(_LEDGER_FLOW_EXEMPT)   # (爱群号,佳宝号)


def test_boguan_accounts_seed(db_session):
    """博冠货款户 = 爱群号 + 主力号(用户: 博冠的账只走李爱群+主账号)。"""
    reg.invalidate()
    assert set(reg.boguan_accounts(db_session)) == {"爱群号", "主力号"}


def test_role_helpers(db_session):
    reg.invalidate()
    assert reg.is_revenue_account(db_session, "企业号") is True
    assert reg.is_revenue_account(db_session, "爱群号") is False
    assert reg.is_internal_account(db_session, "个体户私账") is True
    assert reg.is_internal_account(db_session, "企业号") is False
    assert reg.is_boguan_account(db_session, "主力号") is True
    assert reg.roles_of(db_session, "未登记新账户") == set()   # 未登记 → 空集(默认按经营处理)


def test_config_overrides_seed(db_session):
    """写 account_roles 配置 + invalidate → 覆盖种子。"""
    import json
    settings_service.set_value(db_session, "account_roles",
                               json.dumps({"测试户": ["boguan_payment"], "企业号": ["revenue"]}))
    db_session.commit()
    reg.invalidate()
    try:
        assert "测试户" in reg.boguan_accounts(db_session)
        assert set(reg.non_revenue_accounts(db_session)) == {"测试户"}   # 只有测试户没 revenue
    finally:
        reg.invalidate()   # 清缓存避免污染其它测试
