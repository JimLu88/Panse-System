"""清空导入业务数据 (保留所有设置/配置/账号).

设计原则: 用「白名单」列出要清的业务数据表 —— 任何设置/配置/账号表
都不在这个列表里, 所以绝不会被误删。新增业务表时需手动加入此列表。

绝不清空 (不在 _BUSINESS_TABLES 里, 永不触碰):
  - users                 登录账号
  - system_settings       系统配置 (AI key / 阈值 / 各项开关)
  - feishu_table_bindings 飞书表绑定
  - feishu_sync_map       飞书同步映射
  - alembic_version       数据库迁移版本
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

_logger = logging.getLogger("panse.data_reset")

# 要清空的业务数据表 (按外键依赖顺序: 子表在前, 父表在后)。
# 注意: 这是白名单 —— 只有列在这里的表会被清空。
_BUSINESS_TABLES: list[str] = [
    # ---- 导入明细 / 子表 ----
    "delivery_note_lines",
    "delivery_notes",
    "delivery_files",
    "purchase_files",
    "order_details",
    "order_events",
    "inventory_lock_ledger",
    # ---- 财务流水 ----
    "alipay_flows",
    "account_balances",
    "factory_reconciliations",
    "wanshifu_bills",
    "logistics_bills",
    "promotion_flows",
    "outsourcing_expenses",
    "refill_records",
    "part_purchases",
    # ---- 订单 / 工厂 ----
    "orders",
    "factory_orders",
    # ---- 产品 / 物料 / BOM / 定价 ----
    "bom_lines",
    "pricing_sku_costs",
    "pricing_sku_promo",
    "pricing_sku",
    "products",
    "materials",
    "custom_variants",
    "taobao_listings",
    # ---- 库存 ----
    "product_inventory",
    "part_inventory",
    # ---- 售后 / 营销 / 样品 ----
    "after_sales",
    "brand_marketing",
    "samples",
    "wood_losses",
    "daily_operations",
    "competitor_prices",
    # ---- 客户 / 供应商 ----
    "customers",
    "suppliers",
    "supplier_scores",
    # ---- 汇总 / 衍生 ----
    "sales_daily_rollup",
    "daily_briefings",
    "accounting_periods",
    # ---- 异常 / 导入作业 ----
    "data_exceptions",
    "import_jobs",
    "alerts",
]


def list_business_tables() -> list[str]:
    """返回会被清空的业务表清单 (供前端展示给用户确认)。"""
    return list(_BUSINESS_TABLES)


def reset_business_data(db: Session) -> dict[str, int]:
    """清空所有导入业务数据, 保留账号/设置/配置。

    返回 {table_name: deleted_rows}。
    """
    deleted: dict[str, int] = {}
    bind = db.get_bind()
    dialect = bind.dialect.name if bind is not None else ""

    # 关外键约束 (SQLite 用 PRAGMA, Postgres 用 session_replication_role 需超级权限,
    # 这里按依赖顺序删除, 无需强关)。
    if dialect == "sqlite":
        db.execute(text("PRAGMA foreign_keys = OFF"))

    for table in _BUSINESS_TABLES:
        try:
            # 先数行数 (给用户看清了多少)
            cnt = db.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
            db.execute(text(f'DELETE FROM "{table}"'))
            deleted[table] = int(cnt)
        except Exception as e:  # 表不存在等情况跳过, 不中断整体清空
            _logger.warning("清空 %s 跳过: %s", table, e)
            deleted[table] = 0

    if dialect == "sqlite":
        db.execute(text("PRAGMA foreign_keys = ON"))

    db.commit()
    total = sum(deleted.values())
    _logger.info("业务数据已清空: 共 %d 行 (%d 张表)", total, len(_BUSINESS_TABLES))
    return deleted


def reset_feishu_data(db: Session) -> dict[str, int]:
    """清空飞书云端 (多维表格) 里所有已绑定表的记录 + 本地行级映射。

    高危、不可逆: 会调用飞书 API 逐表删除记录。
    - 遍历所有 FeishuTableBinding (不论 enabled), 拉取每张表全部记录并批量删除
    - 删完后清空本地 feishu_sync_map (行级映射已失效)
    返回 {system_table: 删除的飞书记录数}。飞书未配置则抛 FeishuError。
    """
    from app.models.feishu_sync import FeishuTableBinding
    from app.services import feishu_client

    # 未配置凭证直接抛错 (上层捕获提示用户)
    feishu_client.get_credentials(db)

    deleted: dict[str, int] = {}
    bindings = db.query(FeishuTableBinding).all()
    for b in bindings:
        try:
            records = feishu_client.list_records(db, b.feishu_app_token, b.feishu_table_id)
            rec_ids = [r["record_id"] for r in records if r.get("record_id")]
            n = feishu_client.batch_delete_records(
                db, b.feishu_app_token, b.feishu_table_id, rec_ids
            )
            deleted[b.system_table] = deleted.get(b.system_table, 0) + n
            _logger.info("清空飞书表 %s (%s): %d 条", b.system_table, b.feishu_table_id, n)
        except Exception as e:  # 单表失败不中断其余表
            _logger.warning("清空飞书表 %s 失败: %s", b.system_table, e)
            deleted.setdefault(b.system_table, 0)

    # 行级映射已失效, 一并清掉 (绑定关系保留)
    map_cnt = db.execute(text("SELECT COUNT(*) FROM feishu_sync_map")).scalar() or 0
    db.execute(text("DELETE FROM feishu_sync_map"))
    db.commit()
    deleted["_feishu_sync_map"] = int(map_cnt)
    _logger.info("飞书云端已清空: 共 %d 条记录", sum(deleted.values()))
    return deleted
