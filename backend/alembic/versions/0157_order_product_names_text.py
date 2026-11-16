"""Keep complete marketplace titles across parent, child and factory orders."""
from alembic import op
import sqlalchemy as sa
revision = "0157"
down_revision = "0156"
branch_labels = None
depends_on = None
TABLES = ("orders", "order_details", "factory_orders")
def _detach_views():
    """Preserve live definitions/ACL in the migration transaction; never CASCADE."""
    connection = op.get_bind()
    views = connection.execute(sa.text("""
        SELECT c.relname, pg_get_viewdef(c.oid, true) AS definition,
               pg_get_userbyid(c.relowner) AS owner, c.reloptions,
               obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind='v'
          AND c.relname IN ('chatbi_v_orders','chatbi_v_daily_sales')
        ORDER BY c.relname
    """)).mappings().all()
    result = []
    quote = connection.dialect.identifier_preparer.quote
    for view in views:
        item = dict(view)
        item['grants'] = connection.execute(sa.text("""
            SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
                   a.privilege_type, a.is_grantable
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
                 LATERAL aclexplode(coalesce(c.relacl, acldefault('r',c.relowner))) a
            WHERE n.nspname='public' AND c.relname=:name
        """), {'name': item['relname']}).mappings().all()
        result.append(item)
    for item in result:
        op.execute(sa.text('DROP VIEW public.' + quote(item['relname'])))
    return result


def _restore_views(views):
    connection = op.get_bind()
    quote = connection.dialect.identifier_preparer.quote
    for view in views:
        name = 'public.' + quote(view['relname'])
        options = ' WITH (' + ','.join(view['reloptions']) + ')' if view['reloptions'] else ''
        op.execute(sa.text('CREATE VIEW ' + name + options + ' AS ' + view['definition']))
        op.execute(sa.text('ALTER VIEW ' + name + ' OWNER TO ' + quote(view['owner'])))
        if view['comment'] is not None:
            literal = str(sa.literal(view['comment']).compile(dialect=connection.dialect,
                         compile_kwargs={'literal_binds': True}))
            op.execute(sa.text('COMMENT ON VIEW ' + name + ' IS ' + literal))
        for grant in view['grants']:
            role = 'PUBLIC' if grant['grantee'] == 'PUBLIC' else quote(grant['grantee'])
            suffix = ' WITH GRANT OPTION' if grant['is_grantable'] else ''
            op.execute(sa.text('GRANT ' + grant['privilege_type'] + ' ON ' + name + ' TO ' + role + suffix))


def upgrade():
    views = _detach_views()
    for table in TABLES:
        op.alter_column(table, "product_name", existing_type=sa.String(255), type_=sa.Text(), existing_nullable=True)
    _restore_views(views)
def downgrade():
    # Refuse rollback if it would truncate a title. Data must never be shortened.
    connection = op.get_bind()
    for table in TABLES:
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table} WHERE length(product_name)>255")).scalar()
        if count:
            raise RuntimeError("Cannot narrow product_name while complete long titles exist")
    views = _detach_views()
    for table in TABLES:
        op.alter_column(table, "product_name", existing_type=sa.Text(), type_=sa.String(255), existing_nullable=True)
    _restore_views(views)
