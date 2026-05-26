from app.models.material import Material
from app.services import material_coder


def _add(db, code, name, is_custom=False):
    db.add(Material(code=code, name=name, is_custom=is_custom))
    db.flush()


def test_next_custom_code_empty_db(db_session):
    assert material_coder.next_custom_code(db_session) == "AC-1000"


def test_next_custom_code_ignores_standard_codes(db_session):
    _add(db_session, "AC-0001", "标准件 A")
    _add(db_session, "AC-0196", "标准件 B")
    assert material_coder.next_custom_code(db_session) == "AC-1000"


def test_next_custom_code_takes_max_plus_one(db_session):
    _add(db_session, "AC-1001", "定制 A", is_custom=True)
    _add(db_session, "AC-1003", "定制 B", is_custom=True)
    _add(db_session, "AC-1005", "定制 C", is_custom=True)
    assert material_coder.next_custom_code(db_session) == "AC-1006"


def test_next_custom_code_holes_not_reused(db_session):
    _add(db_session, "AC-1001", "定制 A", is_custom=True)
    _add(db_session, "AC-1005", "定制 C", is_custom=True)
    # 即便 1002~1004 是空洞也不复用，避免歧义
    assert material_coder.next_custom_code(db_session) == "AC-1006"


def test_parse_code():
    assert material_coder.parse_code("AC-0001") == ("AC", 1)
    assert material_coder.parse_code("AC-1006") == ("AC", 1006)
    assert material_coder.parse_code("not-a-code") is None
    assert material_coder.parse_code("") is None
