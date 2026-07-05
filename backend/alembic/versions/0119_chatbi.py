"""ChatBI 问数 (Plan4 v2): 白名单只读视图 chatbi_v_* + 审计表 chatbi_queries。

视图是 LLM 唯一可见面 (脱敏: 无收件人/电话/地址; is_settled_sale 物化 settled_sale_clause 口径)。
只读角色 chatbi_ro 由 scripts/chatbi_create_ro.sql 单独建 (需超级用户); 本迁移的 GRANT 只在角色
已存在时执行, 故建角色前后跑本迁移都安全。纯建视图/表, 幂等。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None

_SETTLED = ("status IN ('paid','shipped','signed','completed','success','finished') "
            "AND COALESCE(paid_amount,0) > 0 "
            "AND NOT (COALESCE(refund_amount,0) >= COALESCE(paid_amount,0) * 0.99)")

_VIEW_ORDERS = f"""
CREATE OR REPLACE VIEW chatbi_v_orders AS
SELECT
    order_no, order_date, ship_date, product_code, product_name, sku_code, qty,
    paid_amount, refund_amount, status, platform, shop, is_refill,
    ({_SETTLED}) AS is_settled_sale
FROM orders
"""

_VIEW_PRODUCTS = """
CREATE OR REPLACE VIEW chatbi_v_products AS
SELECT code, name, sku_code, category FROM products
"""

_VIEW_DAILY = f"""
CREATE OR REPLACE VIEW chatbi_v_daily_sales AS
SELECT order_date AS sale_day, product_code,
       MAX(product_name) AS product_name,
       SUM(qty) AS qty,
       SUM(COALESCE(paid_amount,0) - COALESCE(refund_amount,0)) AS revenue
FROM orders
WHERE is_refill = false AND order_date IS NOT NULL AND ({_SETTLED})
GROUP BY order_date, product_code
"""

_GRANT_RO = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbi_ro') THEN
    GRANT SELECT ON chatbi_v_orders, chatbi_v_products, chatbi_v_daily_sales TO chatbi_ro;
  END IF;
END $$;
"""


def _has_table(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade() -> None:
    # 审计表 (幂等)
    if not _has_table("chatbi_queries"):
        op.create_table(
            "chatbi_queries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(64), nullable=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("route", sa.String(16), nullable=False),
            sa.Column("template_key", sa.String(64), nullable=True),
            sa.Column("sql_text", sa.Text(), nullable=True),
            sa.Column("sql_fingerprint", sa.String(64), nullable=True),
            sa.Column("row_count", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("llm_model", sa.String(64), nullable=True),
            sa.Column("feedback", sa.String(8), nullable=True),
            sa.Column("feedback_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        )
        op.create_index("ix_chatbi_queries_route", "chatbi_queries", ["route"])
        op.create_index("ix_chatbi_queries_username", "chatbi_queries", ["username"])
        op.create_index("ix_chatbi_queries_created", "chatbi_queries", ["created_at"])

    # 视图 + 授权只在 Postgres 上建 (sqlite 测试库不跑迁移, 走 create_all)
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_VIEW_ORDERS)
        op.execute(_VIEW_PRODUCTS)
        op.execute(_VIEW_DAILY)
        op.execute(_GRANT_RO)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP VIEW IF EXISTS chatbi_v_daily_sales")
        op.execute("DROP VIEW IF EXISTS chatbi_v_products")
        op.execute("DROP VIEW IF EXISTS chatbi_v_orders")
    if _has_table("chatbi_queries"):
        op.drop_table("chatbi_queries")
