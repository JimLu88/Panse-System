"""Phase2：采集/爆文/评论管理/舆情/日历/养号强化/团队。"""
import datetime as dt

from app.models import Account, Draft, PublishEvent, Topic


def _published_note(client, db):
    """造一篇已发布笔记，返回 (content_id, account_id)。"""
    t = Topic(title="P2选题", category="餐桌", keywords=["实木餐桌"])
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="x", body="实木餐桌1.4米", tags=["实木餐桌"],
              ai_likeness=10, compliance={"S": [], "A": [], "B": []}, status="approved")
    db.add(d)
    db.commit()
    acc = db.scalar(__import__("sqlalchemy").select(Account).where(Account.stage == "active"))
    ev = PublishEvent(content_id=d.id, account_id=acc.id, result="success",
                      published_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))
    db.add(ev)
    db.commit()
    return d.id, acc.id


# ---------------- #6/#7/#10 爆文挖掘 ----------------
def test_hot_notes_mining(client):
    client.post("/api/crawl/hot-notes?category=餐桌")
    notes = client.get("/api/crawl/hot-notes").json()
    assert notes  # 有爆文（幂等：seed 已预填也算）
    low = client.get("/api/crawl/hot-notes?low_fan=true").json()
    assert all(h["is_low_fan_hit"] for h in low)
    cloud = client.get("/api/crawl/comment-cloud").json()
    assert isinstance(cloud, list)


# ---------------- #16 数据自动回采 ----------------
def test_auto_collect_metrics(client, db):
    _published_note(client, db)
    r = client.post("/api/metrics/auto-collect").json()
    assert r["collected"] >= 1
    # 再调一次应为0（已有数据）
    assert client.post("/api/metrics/auto-collect").json()["collected"] == 0


# ---------------- #17/#18/#20 评论管理 ----------------
def test_inbox_fetch_classify_lead(client, db):
    _published_note(client, db)
    r = client.post("/api/inbox/fetch").json()
    assert r["added"] >= 1
    comments = client.get("/api/inbox/comments").json()
    assert comments
    assert all("intent" in c and c["reply_draft"] for c in comments)
    # 找一条问价/尺寸转线索
    hi = [c for c in comments if c["intent"] in ("price", "size", "material")]
    if hi:
        res = client.post(f"/api/inbox/comments/{hi[0]['id']}/to-lead").json()
        assert res["lead_id"]
        leads = client.get("/api/leads").json()
        assert any(l["id"] == res["lead_id"] for l in leads)
    # 楼中楼
    cid = comments[0]["id"]
    thread = client.post(f"/api/inbox/comments/{cid}/thread",
                         json={"text": "那有现货吗"}).json()
    assert thread["parent_id"] == cid


def test_intent_classification():
    from app.services.inbox_comments import classify_intent
    assert classify_intent("这个多少钱") == "price"
    assert classify_intent("1.4米够用吗") == "size"
    assert classify_intent("榉木好还是橡木好") == "material"
    assert classify_intent("色差好大踩雷了") == "complaint"
    assert classify_intent("好看喜欢") == "praise"


# ---------------- #19 舆情 ----------------
def test_mentions(client):
    client.post("/api/mentions/scan")
    ms = client.get("/api/mentions").json()
    assert ms and all("suggest" in m for m in ms)
    client.post(f"/api/mentions/{ms[0]['id']}/handled")
    assert {m["id"]: m for m in client.get("/api/mentions").json()}[ms[0]["id"]]["status"] == "handled"


# ---------------- #12/#13 日历/最佳时间 ----------------
def test_calendar_and_best_time(client, db):
    cid, aid = _published_note(client, db)
    cal = client.get("/api/dispatch/calendar").json()
    assert cal["accounts"] and cal["dates"]
    bt = client.get(f"/api/dispatch/best-time/{aid}").json()
    assert "best_hours" in bt and len(bt["best_hours"]) >= 1


# ---------------- #9/#11 标题AB/口播 ----------------
def test_title_variants_and_video(client, db):
    t = Topic(title="视频测试", category="餐桌", keywords=["岩板餐桌"])
    db.add(t)
    db.commit()
    acc = client.get("/api/accounts").json()[0]["id"]
    d = client.post("/api/drafts/generate", json={"topic_id": t.id, "account_id": acc}).json()
    tv = client.post(f"/api/drafts/{d['id']}/title-variants").json()
    assert len(tv["variants"]) >= 2
    vid = client.post("/api/drafts/video", json={"topic_id": t.id, "account_id": acc}).json()
    assert vid["content_type"] == "video"


def test_libraries(client):
    assert len(client.get("/api/library/covers").json()) >= 3
    assert "餐桌" in client.get("/api/library/seo").json()


# ---------------- #1/#2 养号强化 ----------------
def test_nurture_matrix_and_diff(client, db):
    a1 = Account(nickname="P2号A", stage="active", health_flag="green",
                 stage_since=dt.date.today(), follower_count=2000)
    a2 = Account(nickname="P2号B", stage="active", health_flag="green",
                 stage_since=dt.date.today(), follower_count=2000)
    db.add_all([a1, a2])
    db.commit()
    t1 = client.get(f"/api/nurture/{a1.id}/today").json()
    # 含矩阵互动任务
    assert any(x["key"] == "matrix" for x in t1["tasks"])


# ---------------- #3/#4 风控/设备 ----------------
def test_risk_signal_circuit_breaker(client, db):
    a = Account(nickname="风控号", stage="active", health_flag="green",
                stage_since=dt.date.today(), follower_count=2000, health_score=100)
    db.add(a)
    db.commit()
    r = client.post(f"/api/accounts/{a.id}/risk-signal", json={"signal": "captcha"}).json()
    assert r["health_score"] == 60  # 100-40
    # 再来一次跌破50 → 红牌熔断
    r2 = client.post(f"/api/accounts/{a.id}/risk-signal", json={"signal": "throttled"}).json()
    assert r2["health_flag"] == "red"


def test_device_conflicts(client, db):
    a1 = Account(nickname="同机1", device_note="iPhone-X", stage="active", health_flag="green",
                 stage_since=dt.date.today())
    a2 = Account(nickname="同机2", device_note="iPhone-X", stage="active", health_flag="green",
                 stage_since=dt.date.today())
    db.add_all([a1, a2])
    db.commit()
    conflicts = client.get("/api/accounts/device-conflicts").json()
    assert any(c["device"] == "iPhone-X" and len(c["accounts"]) >= 2 for c in conflicts)


# ---------------- #5 试发期严审 ----------------
def test_trial_strict_compliance(client, db):
    a = Account(nickname="试发号", stage="trial", health_flag="green",
                stage_since=dt.date.today(), follower_count=500)
    db.add(a)
    db.commit()
    t = Topic(title="严审", category="餐桌")
    db.add(t)
    db.commit()
    # A级敏感词的稿
    d = Draft(topic_id=t.id, title="x", body="全网最低价", tags=[], ai_likeness=10,
              compliance={"S": [], "A": ["全网最低"], "B": []}, status="approved")
    db.add(d)
    db.commit()
    evs = client.post("/api/dispatch/schedule",
                      json={"content_id": d.id, "account_ids": [a.id]}).json()
    assert evs == []  # 试发期 + A级 → 被严审拦下，不排发布


# ---------------- #14 团队 ----------------
def test_team_roles_and_assign(client, db):
    roles = client.get("/api/team/roles").json()
    assert "writer" in roles and "reviewer" in roles
    t = Topic(title="指派", category="床")
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="x", body="b", compliance={"S": [], "A": [], "B": []})
    db.add(d)
    db.commit()
    r = client.patch(f"/api/drafts/{d.id}/assign", json={"assignee": "小李"}).json()
    assert r["assignee"] == "小李"
