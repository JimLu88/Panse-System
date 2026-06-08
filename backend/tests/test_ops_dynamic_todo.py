"""运营待办动态项: 功能 C 对账新差异自动挂待办的承载机制。"""
from app.services import ops_checklist_service as ops


def _find(st, key):
    for g in st["groups"]:
        for t in g["tasks"]:
            if t["key"] == key:
                return t, g["freq"]
    return None, None


def test_add_dynamic_todo_appears_in_status(db_session):
    db = db_session
    ops.add_dynamic_todo(db, key="recon_diff_followup", title="归因做平 3 笔对账差异",
                         detail="去对账诊断", route="/recon-diagnostics", freq="daily")
    db.flush()
    t, freq = _find(ops.status(db), "recon_diff_followup")
    assert t is not None and freq == "daily"
    assert t["dynamic"] is True
    assert t["title"] == "归因做平 3 笔对账差异"
    assert t["done"] is False


def test_add_dynamic_todo_upserts_by_key(db_session):
    db = db_session
    ops.add_dynamic_todo(db, key="k1", title="旧", freq="daily")
    ops.add_dynamic_todo(db, key="k1", title="新", freq="daily")
    db.flush()
    matches = [t for g in ops.status(db)["groups"] for t in g["tasks"] if t["key"] == "k1"]
    assert len(matches) == 1
    assert matches[0]["title"] == "新"


def test_toggle_and_remove_dynamic_todo(db_session):
    db = db_session
    ops.add_dynamic_todo(db, key="k2", title="待办", freq="daily")
    db.flush()
    ops.toggle(db, "k2", True)
    t, _ = _find(ops.status(db), "k2")
    assert t["done"] is True

    ops.remove_dynamic_todo(db, "k2")
    db.flush()
    t, _ = _find(ops.status(db), "k2")
    assert t is None


def test_toggle_unknown_key_raises(db_session):
    db = db_session
    try:
        ops.toggle(db, "does_not_exist", True)
        assert False, "未知任务应报错"
    except ValueError:
        pass
