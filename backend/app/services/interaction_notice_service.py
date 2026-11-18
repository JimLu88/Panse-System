"""One notice per unresolved human-input gate, independent of calendar days.

Only notice state lives here. No credentials, collection retries or business
success markers are changed. A send of unknown outcome consumes its slot too.
"""
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.settings import SystemSetting

SHIPPING_PASSWORD = "shipping_password"
ALIPAY_LOGIN = "alipay_main_login"
PREFIX = "interaction_notice:"


def claim(db, key: str) -> bool:
    if key not in {SHIPPING_PASSWORD, ALIPAY_LOGIN}:
        raise ValueError("Unknown human-input notice")
    bind = db.get_bind()
    insert = pg_insert if bind.dialect.name == "postgresql" else sqlite_insert
    statement = insert(SystemSetting).values(
        key=PREFIX + key, value_plain="waiting", is_secret=False,
    ).on_conflict_do_update(
        index_elements=[SystemSetting.key], set_={"value_plain": "waiting"},
        where=SystemSetting.value_plain == "resolved",
    )
    if bind.dialect.name == "sqlite":
        return db.execute(statement).rowcount == 1
    # Persist before sending; caller rollback/restarts must not resend.
    with bind.begin() as connection:
        return connection.execute(statement).rowcount == 1


def waiting(db, key: str) -> bool:
    return db.query(SystemSetting.value_plain).filter_by(key=PREFIX + key).scalar() == "waiting"


def resolve(db, key: str) -> None:
    # The business caller commits this together with its verified success.
    db.execute(update(SystemSetting).where(SystemSetting.key == PREFIX + key)
               .values(value_plain="resolved"))
