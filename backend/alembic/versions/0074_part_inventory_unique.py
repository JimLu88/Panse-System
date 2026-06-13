"""part_inventory: (warehouse, material_code) 唯一约束 (Plan C6 发现的并发首锁竞态兜底)。

先把存量重复行合并 (qty 求和进最小 id 行, 删其余), 再建唯一约束。
幂等: 约束已存在则跳过。

Revision ID: 0074
Revises: 0073
"""
import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

_UQ = "uq_part_inventory_wh_code"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = {c["name"] for c in insp.get_unique_constraints("part_inventory")}
    if _UQ in existing:
        return
    if bind.dialect.name == "postgresql":
        # 合并重复行: qty 三列求和写进每组最小 id 行
        bind.execute(sa.text("""
            UPDATE part_inventory p SET
                physical_qty = s.physical_qty,
                locked_qty = s.locked_qty,
                defective_qty = s.defective_qty
            FROM (
                SELECT MIN(id) AS keep_id,
                       SUM(physical_qty) AS physical_qty,
                       SUM(locked_qty) AS locked_qty,
                       SUM(defective_qty) AS defective_qty
                FROM part_inventory
                GROUP BY warehouse, material_code
                HAVING COUNT(id) > 1
            ) s
            WHERE p.id = s.keep_id
        """))
        bind.execute(sa.text("""
            DELETE FROM part_inventory p USING (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY warehouse, material_code ORDER BY id) AS rn
                FROM part_inventory
            ) d
            WHERE p.id = d.id AND d.rn > 1
        """))
    op.create_unique_constraint(_UQ, "part_inventory", ["warehouse", "material_code"])


def downgrade() -> None:
    op.drop_constraint(_UQ, "part_inventory", type_="unique")
