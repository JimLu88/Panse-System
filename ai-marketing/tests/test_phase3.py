"""Phase3 收尾：运行时设置 / 真实数据导入 / 周报。"""


def test_runtime_settings(client):
    ss = client.get("/api/settings").json()
    keys = {s["key"] for s in ss}
    assert {"crawler_base_url", "feishu_webhook_url", "erp_base_url"} <= keys
    # 设置后即时生效
    client.post("/api/settings/feishu_webhook_url", json={"value": "https://x.test/hook"})
    after = {s["key"]: s for s in client.get("/api/settings").json()}
    assert after["feishu_webhook_url"]["value"] == "https://x.test/hook"
    assert after["feishu_webhook_url"]["configured"] is True
    # 清空
    client.post("/api/settings/feishu_webhook_url", json={"value": ""})


def test_settings_reject_unknown(client):
    r = client.post("/api/settings/not_a_key", json={"value": "x"})
    assert r.status_code == 400


def test_crawl_import(client):
    payload = {
        "hot_notes": [{"title": "导入的爆文测试", "fans": 500, "likes": 9000,
                       "cover": "大字报", "structure": "钩子→干货",
                       "comments_sample": ["多少钱", "尺寸多大"]}],
        "mentions": [{"type": "brand", "title": "导入舆情测试", "snippet": "畔色不错",
                      "sentiment": "pos"}],
    }
    r = client.post("/api/crawl/import", json=payload).json()
    assert r["hot_notes_added"] == 1
    assert r["mentions_added"] == 1
    # 低粉爆文判定生效（500粉+9000赞）
    low = client.get("/api/crawl/hot-notes?low_fan=true").json()
    assert any(h["title"] == "导入的爆文测试" for h in low)


def test_comment_cloud_tokenizer(client):
    """词云切词：去掉尾部语气词后是干净关键词。"""
    client.post("/api/crawl/import", json={"hot_notes": [
        {"title": "切词测试", "fans": 100, "likes": 100,
         "comments_sample": ["这个够用吗", "尺寸多大呢", "甲醛大不大吗"]}]})
    cloud = client.get("/api/crawl/comment-cloud").json()
    words = {c["word"] for c in cloud}
    # "够用吗"应被切成"够用"，不带尾部"吗"
    assert not any(w.endswith("吗") for w in words), words


def test_weekly_report(client, db):
    import datetime as dt
    from app.models import Account, Draft, Lead, Metric, PublishEvent, Topic
    t = Topic(title="周报选题", category="餐桌")
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="周报内容", body="b", compliance={"S": [], "A": [], "B": []})
    db.add(d)
    db.commit()
    acc = client.get("/api/accounts").json()[0]["id"]
    db.add(PublishEvent(content_id=d.id, account_id=acc, result="success",
                        published_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)))
    db.add(Metric(content_id=d.id, account_id=acc, realness_score=0.8, views=2000,
                  weight_factor=1.0))
    db.add(Lead(source_type="comment", status="won", erp_order_no="SO9"))
    db.commit()
    r = client.get("/api/report/weekly").json()
    assert r["published"] >= 1
    assert "avg_realness" in r and "leads_won" in r
    # 推送（未配飞书 → pushed False，但不报错）
    p = client.post("/api/report/weekly/push").json()
    assert p["pushed_to_feishu"] is False
    assert "report" in p
