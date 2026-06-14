"""端到端：选题→生成→审核→排发布→发布→指标→评论→养号→线索。"""


def test_full_pipeline(client):
    # ① 选题（含常青/时效 + safe_window）
    topics = client.post("/api/topics/generate",
                         json={"category": "餐桌", "count": 3}).json()
    assert topics, "应生成选题"
    assert all(t["kind"] in ("evergreen", "trend") for t in topics)

    accs = client.get("/api/accounts").json()
    active = [a["id"] for a in accs if a["stage"] == "active" and a["health_flag"] == "green"]
    assert len(active) >= 2

    # ③ 生成草稿
    d = client.post("/api/drafts/generate",
                    json={"topic_id": topics[0]["id"], "account_id": active[0]}).json()
    assert d["status"] == "drafted"

    # ③.5 审核：报告含事实核查字段
    rv = client.get(f"/api/review/{d['id']}").json()
    assert "fact_check" in rv
    assert rv["can_approve"], f"mock 草稿应可过审: {rv['blockers']}"
    client.post(f"/api/review/{d['id']}/approve", json={"note": "ok"})

    # ⑥ 排发布：状态机 approved→scheduled
    evs = client.post("/api/dispatch/schedule",
                      json={"content_id": d["id"], "account_ids": active[:2]}).json()
    assert len(evs) == 2
    drafts = {x["id"]: x for x in client.get("/api/drafts").json()}
    assert drafts[d["id"]]["status"] == "scheduled"

    # 重复排程被拒且文案正确
    r2 = client.post("/api/dispatch/schedule",
                     json={"content_id": d["id"], "account_ids": active[:2]})
    assert r2.status_code == 400
    assert "发布队列" in r2.json()["detail"]

    # 发布队列视图 + 卡片
    q = client.get("/api/dispatch/queue").json()
    assert any(e["event_id"] == evs[0]["event_id"] for e in q)
    card = client.get(f"/api/dispatch/{evs[0]['event_id']}/card").json()
    assert set(card["clipboard"]) == {"1_title", "2_body", "3_tags"}

    # 全部发完 → draft=published
    for e in evs:
        client.post(f"/api/dispatch/{e['event_id']}/published")
    drafts = {x["id"]: x for x in client.get("/api/drafts").json()}
    assert drafts[d["id"]]["status"] == "published"

    # ⑦ 指标 + 加权大盘
    client.post("/api/metrics", json={"content_id": d["id"], "account_id": active[0],
                                      "views": 4500, "likes": 300, "comments": 47,
                                      "collects": 189, "question_rate": 0.8,
                                      "interaction_rate": 0.7, "long_comment_ratio": 0.8})
    ov = client.get("/api/analytics/overview").json()
    assert ov["published"] >= 2
    assert "avg_realness_weighted" in ov

    # ⑦→① 反哺：高真实感品类出现 boost
    boost = client.get("/api/analytics/category-boost").json()
    assert boost.get("餐桌", 0) > 0, "真实感>0.6 应产生选题加权"

    # 事件时间线可读
    events = client.get(f"/api/content/{d['id']}/events").json()
    types = [e["event_type"] for e in events]
    assert "draft_generated" in types and "human_approved" in types and "published" in types

    # ⑩ 线索 + 成交回写 + CSV 导出
    lead = client.post("/api/leads", json={"source_type": "comment", "question": "有货吗",
                                           "attribution_code": "畔色XYZ"}).json()
    client.post(f"/api/leads/{lead['id']}/won", json={"erp_order_no": "SO001"})
    csv_text = client.get("/api/leads/export").text
    assert "SO001" in csv_text

    # digest 可用
    digest = client.get("/api/digest").json()
    assert "overdue_leads" in digest
