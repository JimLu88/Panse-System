"""回归用例：覆盖评审发现的逻辑 bug，防止复发。"""
import datetime as dt

from app.models import Account, CommentOpportunity, Draft, Topic


def _mk_topic(db, title="回归测试选题"):
    t = Topic(title=title, category="餐桌")
    db.add(t)
    db.commit()
    return t


def test_approve_blocks_high_ai_likeness(client, db):
    """bug#1：AI感≥70 的稿 approve API 必须拒绝（不能只靠 UI 禁用）。"""
    t = _mk_topic(db)
    bad = Draft(topic_id=t.id, title="x", body="首先其次综上所述",
                ai_likeness=95, compliance={"S": [], "A": [], "B": []})
    db.add(bad)
    db.commit()
    r = client.post(f"/api/review/{bad.id}/approve", json={"note": ""})
    assert r.status_code == 400
    assert "AI感" in r.json()["detail"]


def test_approve_blocks_s_level(client, db):
    """S级敏感词命中必须拒绝。"""
    t = _mk_topic(db, "S级测试")
    bad = Draft(topic_id=t.id, title="x", body="甲醛超标",
                ai_likeness=10, compliance={"S": ["甲醛超标"], "A": [], "B": []})
    db.add(bad)
    db.commit()
    r = client.post(f"/api/review/{bad.id}/approve", json={"note": ""})
    assert r.status_code == 400


def test_offsets_unique_for_many_accounts(client, db):
    """bug#5：超过 5 个账号错峰不回绕撞峰，offset 严格递增。"""
    for i in range(7):
        db.add(Account(nickname=f"压测号{i}", stage="active", health_flag="green",
                       stage_since=dt.date.today(), follower_count=2000))
    t = _mk_topic(db, "错峰测试")
    d = Draft(topic_id=t.id, title="t", body="b", tags=["a", "b", "c"],
              ai_likeness=10, compliance={"S": [], "A": [], "B": []}, status="approved")
    db.add(d)
    db.commit()
    ids = [a.id for a in db.query(Account).filter(Account.nickname.like("压测号%"))]
    evs = client.post("/api/dispatch/schedule",
                      json={"content_id": d.id, "account_ids": ids}).json()
    offsets = [e["offset_minutes"] for e in evs]
    assert len(evs) == 7
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == 7, f"offset 出现撞峰: {offsets}"
    assert all(b - a >= 20 for a, b in zip(offsets, offsets[1:]))


def test_comment_scan_dedup(client):
    """bug#4：重复扫描同一批笔记不产生重复机会。"""
    client.post("/api/comments/scan")
    n1 = len(client.get("/api/comments").json())
    client.post("/api/comments/scan")
    n2 = len(client.get("/api/comments").json())
    assert n2 == n1, "扫两次不应翻倍"


def test_comment_daily_limit_and_note_exclusive(client, db):
    """bug#7：单号每日≤5条 + 同笔记矩阵互斥。"""
    acc = Account(nickname="评论压测号", stage="active", health_flag="green",
                  stage_since=dt.date.today(), follower_count=2000)
    db.add(acc)
    db.commit()
    # 同一笔记两个机会：第二个发出应被互斥拒绝
    o1 = CommentOpportunity(note_title="互斥测试笔记", draft_comment="a",
                            compliance={}, suggested_account_id=acc.id)
    o2 = CommentOpportunity(note_title="互斥测试笔记", draft_comment="b",
                            compliance={}, suggested_account_id=acc.id)
    db.add_all([o1, o2])
    db.commit()
    assert client.post(f"/api/comments/{o1.id}/post").status_code == 200
    r = client.post(f"/api/comments/{o2.id}/post")
    assert r.status_code == 400 and "矩阵" in r.json()["detail"]

    # 每日上限：再发 4 条到 5，第 6 条拒绝
    extra = [CommentOpportunity(note_title=f"限额笔记{i}", draft_comment="c",
                                compliance={}, suggested_account_id=acc.id) for i in range(5)]
    db.add_all(extra)
    db.commit()
    results = [client.post(f"/api/comments/{o.id}/post").status_code for o in extra]
    assert results[:4] == [200] * 4
    assert results[4] == 400, "第6条应被每日限额拦截"


def test_promote_requires_checkins(client, db):
    """bug#8：养号期晋级必须有≥10天日活打卡，光躺天数不行。"""
    acc = Account(nickname="躺平号", stage="nurturing", health_flag="green",
                  stage_since=dt.date.today() - dt.timedelta(days=40), follower_count=10)
    db.add(acc)
    db.commit()
    r = client.post(f"/api/nurture/{acc.id}/promote").json()
    assert r["promoted"] is False
    assert "打卡" in r["reason"]


def test_fact_check_extracts_claims():
    """bug#6：事实核查规则版能抽出数值类声明。"""
    from app.services.generator import extract_claims
    claims = extract_claims("选了 1.2米的折叠款，花了 1999元，用了 2年")
    assert len(claims) == 3


def test_weighted_realness(db):
    """bug#9：真实感聚合用冷启动权重（新号0.2不能和大号等权）。"""
    from app.services.analytics import weight_for
    assert weight_for(500) == 0.2
    assert weight_for(5000) == 1.0
    assert weight_for(50000) == 2.5


def test_weekly_profile_task_once(client, db):
    """bug#10：profile 任务每周仅一条，不随日清单重复生成。"""
    acc = Account(nickname="周任务号", stage="nurturing", health_flag="green",
                  stage_since=dt.date.today(), follower_count=10)
    db.add(acc)
    db.commit()
    t1 = client.get(f"/api/nurture/{acc.id}/today").json()
    t2 = client.get(f"/api/nurture/{acc.id}/today").json()
    profiles = [x for x in t2["tasks"] if x["key"] == "profile"]
    assert len(profiles) == 1
    assert t1["total"] == t2["total"]


def test_generator_rejects_unparsable(db, monkeypatch):
    """bug#13：LLM 输出解析失败重试后仍失败 → 拒绝入库，不存空稿。"""
    from app.services import generator
    from app.services.llm_router import LLMRouter
    monkeypatch.setattr(LLMRouter, "complete",
                        lambda self, task, prompt, **k: "这不是JSON")
    t = _mk_topic(db, "解析失败测试")
    try:
        generator.generate_draft(db, t.id)
        raised = False
    except ValueError as e:
        raised = "解析" in str(e)
    assert raised


def test_auth_middleware(monkeypatch):
    """建议#18：配 API_TOKEN 后未带 token 的 /api 请求 401。"""
    from app.config import get_settings
    from fastapi.testclient import TestClient
    from app.main import app
    s = get_settings()
    monkeypatch.setattr(s, "api_token", "secret123")
    c = TestClient(app)
    assert c.get("/api/accounts").status_code == 401
    assert c.get("/api/health").status_code == 200  # health 豁免
    assert c.get("/api/accounts",
                 headers={"authorization": "Bearer secret123"}).status_code == 200
