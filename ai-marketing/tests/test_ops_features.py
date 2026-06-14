"""新增运营功能：今日待办/运营台账/知乎/复盘/合规自查/话术/大促/账号档案/指标条数换算。"""


def test_home_dashboard(client):
    h = client.get("/api/home").json()
    for k in ("to_review", "to_publish", "comments_pending", "nurture_left",
              "overdue_leads", "data_missing", "ops_done", "ops_total"):
        assert k in h


def test_ops_checklist_toggle(client):
    ops = client.get("/api/ops/today").json()
    assert ops["total"] >= 8  # 日/周/月任务都在
    tid = ops["tasks"][0]["id"]
    r = client.post(f"/api/ops/task/{tid}/toggle").json()
    assert r["done"] is True
    again = client.get("/api/ops/today").json()
    assert again["done"] >= 1


def test_zhihu_seeded_and_update(client):
    qs = client.get("/api/zhihu").json()
    assert len(qs) == 20
    qid = qs[0]["id"]
    client.post(f"/api/zhihu/{qid}", json={"status": "writing"})
    client.post(f"/api/zhihu/{qid}", json={"answer_url": "https://zhihu.com/x"})
    qs2 = {q["id"]: q for q in client.get("/api/zhihu").json()}
    assert qs2[qid]["status"] == "writing"
    assert qs2[qid]["answer_url"] == "https://zhihu.com/x"


def test_review_meeting(client):
    client.post("/api/review-meetings", json={"hot_case": "榉木餐桌爆了",
                                              "flop_case": "茶几没人看", "conclusion": "多做餐桌"})
    ms = client.get("/api/review-meetings").json()
    assert ms and ms[0]["hot_case"] == "榉木餐桌爆了"


def test_compliance_check(client):
    r = client.post("/api/compliance/check", json={"text": "全网最低价的纯实木餐桌"}).json()
    assert r["hits"]["A"]  # 命中A级
    assert "改写" in r["verdict"]
    r2 = client.post("/api/compliance/check", json={"text": "甲醛超标"}).json()
    assert r2["blocked"] is True


def test_faq_and_promo(client):
    faq = client.get("/api/faq-scripts").json()
    assert any(f["key"] == "返图" for f in faq)  # 老客返图邀约话术
    promo = client.get("/api/promo-calendar").json()
    assert promo and "seed_start" in promo[0] and "should_seed_now" in promo[0]


def test_account_profile_update(client, db):
    accs = client.get("/api/accounts").json()
    aid = accs[0]["id"]
    r = client.patch(f"/api/accounts/{aid}/profile",
                     json={"real_person": "小王", "device_note": "iPhone13-A",
                           "official_setup": {"企业认证": True}}).json()
    assert r["real_person"] == "小王"
    assert r["official_setup"]["企业认证"] is True


def test_metric_count_to_ratio(client, db):
    """普通人友好：给条数自动换算比例。"""
    from app.models import Account, Draft, Topic
    t = Topic(title="指标换算", category="餐桌")
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="x", body="b", ai_likeness=10,
              compliance={"S": [], "A": [], "B": []})
    db.add(d)
    db.commit()
    acc = client.get("/api/accounts").json()[0]["id"]
    # 10条评论里4条提问、5条长评 → q_rate=0.4, l_ratio=0.5
    m = client.post("/api/metrics", json={"content_id": d.id, "account_id": acc,
                                          "views": 1000, "comments": 10,
                                          "question_comments": 4, "long_comments": 5}).json()
    # realness = 0.4*0.4 + 0.3*0 + 0.3*0.5 = 0.16+0.15 = 0.31
    assert abs(m["realness_score"] - 0.31) < 0.01


def test_datasource_status(client):
    r = client.get("/api/datasource/status").json()
    assert r["mode"] in ("mock", "crawler")


def test_zhihu_generate_answer(client):
    qs = client.get("/api/zhihu").json()
    qid = qs[0]["id"]
    r = client.post(f"/api/zhihu/{qid}/generate").json()
    assert len(r["answer_draft"]) > 100  # 生成了实质内容
    assert r["status"] == "writing"
    # 列表能看到 has_draft
    after = {q["id"]: q for q in client.get("/api/zhihu").json()}
    assert after[qid]["has_draft"] is True


def test_zhihu_generate_all(client):
    r = client.post("/api/zhihu/generate-all").json()
    assert r["generated"] >= 0
    # 再调一次应为0（已全有初稿）
    r2 = client.post("/api/zhihu/generate-all").json()
    assert r2["generated"] == 0
    drafts = [q for q in client.get("/api/zhihu").json() if q["has_draft"]]
    assert len(drafts) == 20


def test_content_seed_batch(client):
    r = client.post("/api/content/seed-batch?per_category=1").json()
    assert r["topics"] >= 5  # 5个品类各至少1个
    assert r["drafts"] >= 1
    drafts = client.get("/api/drafts").json()
    assert len(drafts) >= r["drafts"]


def test_review_suggest(client, db):
    from app.models import Account, Draft, Topic
    t = Topic(title="建议测试", category="床")
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="x", body="b", ai_likeness=10,
              compliance={"S": [], "A": [], "B": []})
    db.add(d)
    db.commit()
    acc = client.get("/api/accounts").json()[0]["id"]
    client.post("/api/metrics", json={"content_id": d.id, "account_id": acc,
                                      "views": 1000, "comments": 10,
                                      "question_comments": 9, "long_comments": 9})
    r = client.post("/api/review-meetings/suggest").json()
    assert "suggestion" in r and len(r["suggestion"]) > 0
