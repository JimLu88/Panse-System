"""子账号页面级权限 (RBAC) — 权威 key 清单 + API 路径→页面 权限映射。

设计 (与前端 frontend/src/auth/permissions.ts 的 permKey 一一对应):
- 每个「可分配页面」有一个 permKey (字符串), 存在 users.page_perms (list) 里。
- users.page_perms == None  → 不受限 (admin / 主账号 / 存量账号一律全看)。
- users.page_perms == [...]  → 只可见列出的 permKey 对应页面, 其余点进去 = 「程序错误」/后端 403。
- role == "admin" 永远不受 page_perms 限制 (主账号=你本人)。

后端这层是「纵深防御」: 前端已按 permKey 过滤菜单 + 路由守卫 (用户看不到也点不进),
这里再对 API 兜底, 防子账号绕过前端直接调接口取数。

映射哲学 (务必读):
- 只精确 gate「干净可 1:1 对应某页面」的路由 + 明确敏感的路由 (财务子路径逐个点名)。
- 多页面共享的诊断类接口 (如 /api/finance 下的 smart-match / 通用诊断、/api/marketing、/api/exports)
  一律 **默认放行** (返回 None): 宁可漏 gate 一个共享接口, 不能错杀合法访问。前端守卫才是页面可见性的权威。
- 未在下表命中的任何 /api/* 路由 → 默认放行 (fail-open), 加接口时按需补规则即可。
- 管理员专属后端 (admin/approvals/scheduler/logs/table-explorer/cs) 映射到哨兵 __admin_only__:
  受限子账号一律 403; admin 走上面的 role 短路放行; operator/viewer 无 page_perms 限制时仍由各端点自带的
  require_role("admin") 兜底 (本层不放宽也不收紧既有角色门)。
"""
from __future__ import annotations

from typing import Optional

# 哨兵: 只有 admin(经 role 短路) 能过; 任何受限子账号都没有这个 key → 403。
ADMIN_ONLY = "__admin_only__"

# ---- 可分配的页面 permKey (必须与前端 permissions.ts 的叶子 key 完全一致) ----
PERM_KEYS: frozenset[str] = frozenset({
    # 数据分析
    "dashboard", "reports",
    # 产品 (BOM尺寸复核 已并入 BOM 清单页做 Tab, 归 bom-list)
    "products", "bom-list", "materials", "new-product", "npd", "taobao-listings",
    # 价格
    "pricing", "customization", "custom-quote-v2",
    # 库存
    "inventory", "product-inventory", "samples", "marketing-wood",
    # 订单 (截图录单 2026-07-01 已停用删除)
    "orders", "orders-kanban", "custom-reconcile", "customers", "aftersales",
    # 物流
    "logistics-bills", "packing-bills", "wanshifu-bills",
    # 营销 (人员外包 2026-07-01 已删, 口径改走财务/人员工资)
    "marketing-promotion", "promotion-flows", "marketing-brand",
    "refill-records", "marketing-daily",
    # 供应链 (供应商评分 已并入供应商页)
    "suppliers", "purchases", "monthly-settlement",
    "factory-orders", "factory-statement", "factory-settlement",
    # 财务
    "assets-cashflow", "alipay", "account-balances", "staff-salary",
    "per-order-reconcile", "recon-center",
    # 顶层
    "ops-checklist",
    # 工具
    "web-agent", "importer", "data-export", "import-archive", "audit-trail", "feishu",
})

# ---- API 路径前缀 → 页面 permKey (None = 永远放行) ----
# 顺序无所谓: 匹配时按前缀长度降序 (最具体优先), 见 _SORTED_RULES。
_API_PERM_RULES: dict[str, Optional[str]] = {
    # --- 永远放行 (登录/探针/AI/告警/搜索/图片/导出按钮) ---
    "/api/auth": None,
    "/api/health": None,
    "/api/ready": None,
    "/api/version": None,
    "/api/ai": None,
    "/api/alerts": None,
    "/api/search": None,
    "/api/gallery": None,
    "/api/exports": None,          # 各页「导出」按钮共用, 页面本身已被前端守卫
    # --- 管理员专属 ---
    "/api/admin": ADMIN_ONLY,
    "/api/approvals": ADMIN_ONLY,
    "/api/scheduler": ADMIN_ONLY,
    "/api/logs": ADMIN_ONLY,
    "/api/table-explorer": ADMIN_ONLY,
    "/api/cs": ADMIN_ONLY,
    # --- 数据分析 ---
    "/api/dashboard": "dashboard",
    "/api/briefings": "dashboard",
    "/api/scanners": "dashboard",
    "/api/exceptions": "dashboard",
    "/api/reports": "reports",
    # --- 产品 ---
    "/api/products": "products",
    "/api/bom": "bom-list",
    "/api/materials": "materials",
    "/api/npd": "npd",
    "/api/taobao-listings": "taobao-listings",
    "/api/taobao-export": "taobao-listings",
    "/api/product-composer": "new-product",
    # --- 价格 ---
    "/api/pricing-skus": "pricing",
    "/api/pricing": "pricing",
    "/api/quotes": "pricing",
    "/api/customization": "customization",
    "/api/competitor": "customization",
    # --- 库存 ---
    "/api/inventory/parts": "inventory",
    "/api/inventory/products": "product-inventory",
    "/api/inventory": "inventory",
    "/api/product-inventory": "product-inventory",
    "/api/producibility": "product-inventory",
    # --- 订单 ---
    "/api/orders": "orders",
    "/api/match": "orders",
    "/api/shipments": "orders",
    "/api/customers": "customers",
    "/api/aftersales": "aftersales",
    "/api/part-returns": "aftersales",
    # 截图录单页已删, 但 /api/screenshots 仍被打包费/工厂对账 OCR 共用 → 放行
    "/api/screenshots": None,
    # --- 营销 (多子页共享, 放行, 前端守卫分 tab) ---
    "/api/marketing": None,
    # --- 供应链 (供应商评分已并入供应商页, 其接口归 suppliers) ---
    "/api/suppliers": "suppliers",
    "/api/supplier-scores": "suppliers",
    "/api/delivery-notes": "suppliers",
    "/api/delivery-files": "suppliers",
    "/api/purchases": "purchases",
    "/api/monthly-settlement": "monthly-settlement",
    "/api/factory-orders": "factory-orders",
    "/api/factory-statement": "factory-statement",
    "/api/factory-settlement": "factory-settlement",
    "/api/factory-recon": "recon-center",
    "/api/settlements": "recon-center",
    # --- 财务: 顶层 /api/finance 是大杂烩, 敏感子路径逐个点名, 其余 (通用诊断) 放行 ---
    "/api/finance/alipay-flows": "alipay",
    "/api/finance/accounts": "account-balances",
    "/api/finance/balances": "account-balances",
    "/api/finance/cash-flow": "assets-cashflow",
    "/api/finance/reconciliation": "recon-center",
    "/api/finance/factory-reconciliation": "recon-center",
    "/api/finance/factory-payment": "factory-settlement",
    "/api/finance/financial-coefficients": "recon-center",
    "/api/finance/wanshifu-bills": "wanshifu-bills",
    "/api/finance/wanshifu-orders": "wanshifu-bills",
    "/api/finance/logistics-bills": "logistics-bills",
    "/api/finance/promotion-flows": "promotion-flows",
    "/api/finance/refill-records": "refill-records",
    "/api/finance/refill-summary": "refill-records",
    "/api/finance": None,          # 其余共享诊断接口放行 (order-flow-match / smart-match / cost-anomaly ...)
    "/api/shop-deposits": "assets-cashflow",
    "/api/staff-salaries": "staff-salary",
    "/api/staff-salary": "staff-salary",
    "/api/accounting": "account-balances",
    # --- 顶层 ---
    "/api/ops-checklist": "ops-checklist",
    # --- 工具 ---
    "/api/importer": "importer",
    "/api/imports": "import-archive",
    "/api/import-archive": "import-archive",
    "/api/audit": "audit-trail",
    "/api/field-changes": "audit-trail",
    "/api/feishu": "feishu",
    "/api/web-agent": "web-agent",
}

# 按前缀长度降序 → 最具体的规则先命中 (/api/finance/alipay-flows 早于 /api/finance,
# /api/supplier-scores 早于 /api/suppliers, /api/inventory/parts 早于 /api/inventory)。
_SORTED_RULES: list[tuple[str, Optional[str]]] = sorted(
    _API_PERM_RULES.items(), key=lambda kv: len(kv[0]), reverse=True
)


def perm_for_path(path: str) -> Optional[str]:
    """给定 API 路径, 返回访问所需的 permKey; None = 永远放行 (未命中也放行, fail-open)。"""
    for prefix, perm in _SORTED_RULES:
        if path == prefix or path.startswith(prefix + "/"):
            return perm
    return None


def is_user_allowed(role: Optional[str], page_perms: Optional[list], path: str) -> bool:
    """该用户能否访问该 API 路径。

    admin / 无 page_perms 限制 → 放行; 命中的 permKey 在其清单内 → 放行; 否则拒。
    """
    if role == "admin":
        return True
    if page_perms is None:            # 不受限 (存量账号 / 未设子账号权限)
        return True
    perm = perm_for_path(path)
    if perm is None:
        return True
    return perm in page_perms


def sanitize_perms(perms: Optional[list]) -> Optional[list]:
    """清洗管理端传入的 page_perms: 只留合法 key, 去重并排序; None 透传 (=不受限)。

    传入 [] (空列表) 视为「什么都看不到」的受限账号, 保留为 []。
    """
    if perms is None:
        return None
    seen = {p for p in perms if isinstance(p, str) and p in PERM_KEYS}
    return sorted(seen)
