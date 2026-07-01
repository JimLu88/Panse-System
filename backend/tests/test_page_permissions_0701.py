"""子账号页面权限 (RBAC, 2026-07-01): 路径→页面映射 / 访问判定 / 清洗 / 用户创建更新持久化。"""
from app import page_permissions as pp
from app.api import auth as auth_api
from app.models.auth import User
from app.services import auth_service

STRONG = "Str0ng-Passw0rd-2026"  # ≥12 位, 字符多样, 不含用户名 — 过密码强度校验


# ---------------- perm_for_path: 前缀映射精度 ----------------

def test_perm_for_path_clean_resources():
    assert pp.perm_for_path("/api/products") == "products"
    assert pp.perm_for_path("/api/products/123") == "products"
    assert pp.perm_for_path("/api/orders/kanban") == "orders"       # 子路由归 orders API


def test_perm_for_path_finance_subprefix_precision():
    # /api/finance 是大杂烩: 敏感子路径逐个精确命中, 顶层其余共享诊断放行
    assert pp.perm_for_path("/api/finance/alipay-flows") == "alipay"
    assert pp.perm_for_path("/api/finance/accounts") == "account-balances"
    assert pp.perm_for_path("/api/finance/cash-flow") == "assets-cashflow"
    assert pp.perm_for_path("/api/finance/reconciliation/writeoff") == "recon-center"
    assert pp.perm_for_path("/api/finance/order-flow-match/backfill") is None  # 共享诊断→放行
    assert pp.perm_for_path("/api/finance") is None


def test_perm_for_path_longest_prefix_wins():
    assert pp.perm_for_path("/api/supplier-scores") == "suppliers"  # 评分已并入供应商页
    assert pp.perm_for_path("/api/suppliers/5") == "suppliers"
    assert pp.perm_for_path("/api/inventory/parts") == "inventory"       # 不被 /api/inventory 抢
    assert pp.perm_for_path("/api/inventory/products") == "product-inventory"


def test_perm_for_path_admin_only_and_allowlist():
    assert pp.perm_for_path("/api/admin/settings") == pp.ADMIN_ONLY
    assert pp.perm_for_path("/api/scheduler/jobs") == pp.ADMIN_ONLY
    assert pp.perm_for_path("/api/auth/login") is None
    assert pp.perm_for_path("/api/health") is None
    assert pp.perm_for_path("/api/some-unmapped-router") is None       # fail-open


# ---------------- is_user_allowed ----------------

def test_is_user_allowed_admin_bypass():
    assert pp.is_user_allowed("admin", [], "/api/finance/alipay-flows") is True  # admin 恒可


def test_is_user_allowed_unrestricted_none():
    assert pp.is_user_allowed("operator", None, "/api/finance/alipay-flows") is True


def test_is_user_allowed_restricted():
    perms = ["orders", "products"]
    assert pp.is_user_allowed("operator", perms, "/api/orders/123") is True
    assert pp.is_user_allowed("operator", perms, "/api/finance/alipay-flows") is False
    assert pp.is_user_allowed("operator", perms, "/api/admin/x") is False   # admin-only 拦住受限号
    assert pp.is_user_allowed("operator", perms, "/api/auth/me") is True    # 放行路径


def test_is_user_allowed_empty_list_sees_only_allowlist():
    assert pp.is_user_allowed("viewer", [], "/api/products") is False
    assert pp.is_user_allowed("viewer", [], "/api/auth/me") is True


# ---------------- sanitize_perms ----------------

def test_sanitize_perms_filters_dedup_sorts():
    assert pp.sanitize_perms(["orders", "products", "orders", "NOPE"]) == ["orders", "products"]


def test_sanitize_perms_none_passthrough():
    assert pp.sanitize_perms(None) is None


def test_sanitize_perms_empty_stays_empty():
    assert pp.sanitize_perms([]) == []


def test_perm_keys_cover_key_pages():
    for k in ["dashboard", "orders", "pricing", "alipay", "recon-center",
              "factory-settlement", "ops-checklist", "custom-quote-v2"]:
        assert k in pp.PERM_KEYS


def test_removed_pages_not_in_perm_keys():
    # 2026-07-01 清理删掉的页面, 不应再作为可分配权限出现
    for k in ["supplier-scores", "bom-size-review", "marketing-outsourcing", "screenshots"]:
        assert k not in pp.PERM_KEYS


# ---------------- 用户创建/更新: page_perms 持久化 (endpoint 直调, 复用 db_session) ----------------

def _make_admin(db):
    a = auth_service.create_user(db, username="boss", password=STRONG, role="admin")
    db.commit()
    return a


def test_create_subaccount_persists_sanitized_perms(db_session):
    admin = _make_admin(db_session)
    out = auth_api.create_user(
        auth_api.UserCreateIn(username="clerk", password=STRONG, role="operator",
                              page_perms=["orders", "products", "BADKEY"]),
        db=db_session, _admin=admin,
    )
    assert out.role == "operator"
    assert out.page_perms == ["orders", "products"]   # 清洗+排序, 非法 key 剔除


def test_create_admin_forced_unrestricted(db_session):
    admin = _make_admin(db_session)
    out = auth_api.create_user(
        auth_api.UserCreateIn(username="admin2", password=STRONG, role="admin",
                              page_perms=["orders"]),
        db=db_session, _admin=admin,
    )
    assert out.page_perms is None                      # admin 恒不受限


def test_meout_exposes_page_perms(db_session):
    admin = _make_admin(db_session)
    out = auth_api.create_user(
        auth_api.UserCreateIn(username="clerk", password=STRONG, role="viewer",
                              page_perms=["reports"]),
        db=db_session, _admin=admin,
    )
    assert out.page_perms == ["reports"]               # MeOut 带出 page_perms


def test_update_perms_only_when_field_present(db_session):
    admin = _make_admin(db_session)
    sub = auth_api.create_user(
        auth_api.UserCreateIn(username="clerk", password=STRONG, role="operator",
                              page_perms=["orders"]),
        db=db_session, _admin=admin,
    )
    sid = sub.id
    # 不带 page_perms 的更新 → 权限保持不变
    auth_api.update_user(sid, auth_api.UserUpdateIn(display_name="小王"),
                         db=db_session, _admin=admin)
    assert db_session.get(User, sid).page_perms == ["orders"]
    # 带 page_perms → 覆盖 (清洗+排序)
    auth_api.update_user(sid, auth_api.UserUpdateIn(page_perms=["pricing", "materials"]),
                         db=db_session, _admin=admin)
    assert db_session.get(User, sid).page_perms == ["materials", "pricing"]
    # 显式传 null → 恢复不受限
    auth_api.update_user(sid, auth_api.UserUpdateIn(page_perms=None),
                         db=db_session, _admin=admin)
    assert db_session.get(User, sid).page_perms is None


def test_promote_to_admin_clears_perms(db_session):
    admin = _make_admin(db_session)
    sub = auth_api.create_user(
        auth_api.UserCreateIn(username="clerk", password=STRONG, role="operator",
                              page_perms=["orders"]),
        db=db_session, _admin=admin,
    )
    out = auth_api.update_user(sub.id, auth_api.UserUpdateIn(role="admin"),
                              db=db_session, _admin=admin)
    assert out.page_perms is None                      # 升级 admin → page_perms 被清空
