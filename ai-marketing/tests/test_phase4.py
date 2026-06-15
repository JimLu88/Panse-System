"""Phase4：数字人(重点)/CRM/A-B/漏斗/切片/个性化/Agent/集成。"""
import datetime as dt

from app.models import Account, Draft, Lead, Topic


# ---------------- #2/#6 数字人（重点）----------------
def test_avatar_auth_gate_and_render(client, db):
    av = client.post("/api/avatars", json={"name": "测试IP", "real_person": "老板",
                                           "face_ref": "f.png", "voice_sample_ref": "v.wav"}).json()
    assert av["status"] == "draft"  # 未授权
    t = Topic(title="数字人选题", category="餐桌", keywords=["餐桌"])
    db.add(t)
    db.commit()
    acc = client.get("/api/accounts").json()[0]["id"]
    vid = client.post("/api/drafts/video", json={"topic_id": t.id, "account_id": acc}).json()
    # 未授权渲染被拒（合规闸）
    r = client.post("/api/avatars/render/draft",
                    json={"draft_id": vid["id"], "avatar_id": av["id"]})
    assert r.status_code == 400 and "授权" in r.json()["detail"]
    # 授权后可渲染
    client.post(f"/api/avatars/{av['id']}/authorize")
    j = client.post("/api/avatars/render/draft",
                    json={"draft_id": vid["id"], "avatar_id": av["id"]}).json()
    assert j["status"] == "done" and j["output_url"]
    jobs = client.get("/api/avatars/jobs").json()
    assert any(x["id"] == j["job_id"] for x in jobs)


def test_avatar_dm_personalized_video(client, db):
    av = client.post("/api/avatars", json={"name": "分身", "face_ref": "f", "authorized": True}).json()
    j = client.post("/api/avatars/render/dm",
                    json={"avatar_id": av["id"], "target": "王女士", "script": "您的餐桌到货了"}).json()
    assert j["status"] == "done"
    jobs = {x["id"]: x for x in client.get("/api/avatars/jobs").json()}
    assert jobs[j["job_id"]]["type"] == "dm" and jobs[j["job_id"]]["target"] == "王女士"


def test_avatar_callback(client, db):
    av = client.post("/api/avatars", json={"name": "回调", "face_ref": "f", "authorized": True}).json()
    j = client.post("/api/avatars/render/dm",
                    json={"avatar_id": av["id"], "target": "x", "script": "y"}).json()
    r = client.post(f"/api/avatars/jobs/{j['job_id']}/callback",
                    json={"output_url": "https://real/v.mp4", "status": "done"}).json()
    assert r["status"] == "done"


# ---------------- #5 CRM ----------------
def test_crm_sync_and_rfm(client, db):
    db.add(Lead(source_type="comment", contact="cust-A", status="won", erp_order_no="O1"))
    db.add(Lead(source_type="comment", contact="cust-A", status="won", erp_order_no="O2"))
    db.add(Lead(source_type="dm", contact="cust-B", status="won", erp_order_no="O3"))
    db.commit()
    r = client.post("/api/crm/sync").json()
    assert r["total"] >= 2
    custs = {c["contact"]: c for c in client.get("/api/crm/customers").json()}
    assert custs["cust-A"]["order_count"] == 2
    assert custs["cust-A"]["rfm_tier"] in ("repeat", "vip")
    assert custs["cust-A"]["reach_suggestion"]


# ---------------- #9 A/B + 漏斗 ----------------
def test_experiment_bandit(client):
    e = client.post("/api/experiments", json={"name": "封面AB", "factor": "cover",
                                              "arms": ["大字报", "实景"]}).json()
    # 空实验推荐探索
    assert client.get(f"/api/experiments/{e['id']}/recommend").json()["recommend"] in ("大字报", "实景")
    client.post(f"/api/experiments/{e['id']}/result", json={"arm": "大字报", "reward": 0.9})
    client.post(f"/api/experiments/{e['id']}/result", json={"arm": "实景", "reward": 0.2})
    w = client.post(f"/api/experiments/{e['id']}/conclude").json()
    assert w["winner"] == "大字报"


def test_funnel(client):
    f = client.get("/api/analytics/funnel").json()
    names = [s["name"] for s in f["stages"]]
    assert "发布笔记" in names and "成交" in names
    assert "线索成交率" in f["conversion"]


# ---------------- #3 切片 ----------------
def test_video_clips(client):
    tr = "今天去工厂看榉木选材。这批含水率控制得很好不易变形。封边工艺很扎实。包装用了五层防护。物流三天就到家了。"
    r = client.post("/api/clips", json={"title": "工厂探访", "transcript": tr,
                                        "category": "餐桌", "max_clips": 4}).json()
    assert r["clips"] >= 2
    drafts = client.get("/api/drafts").json()
    assert any(d["id"] in r["draft_ids"] for d in drafts)


# ---------------- #7 受众个性化 ----------------
def test_personalize(client, db):
    t = Topic(title="个性化选题", category="餐桌", keywords=["餐桌"])
    db.add(t)
    db.commit()
    r = client.post("/api/personalize",
                    json={"topic_id": t.id, "segments": ["小户型", "新中式", "预算有限"]}).json()
    assert r["drafts"] == 3
    segs = client.get("/api/audience/segments").json()
    assert "小户型" in segs


# ---------------- #1 Agent / #4 / #8 / #10 ----------------
def test_agent_actions(client):
    actions = client.get("/api/agent/actions").json()
    names = {a["name"] for a in actions}
    assert {"topics", "draft", "weekly-report"} <= names


def test_cli_dispatch(db):
    from app.cli import _dispatch
    r = _dispatch(db, "topics", ["餐桌", "2"])
    assert len(r) == 2 and "title" in r[0]


def test_automation_events(client):
    ev = client.get("/api/automation/events").json()
    assert "lead.created" in ev and "hot_note.found" in ev


def test_official_publish_gate(client, db):
    """无官方API能力的号 → 拒绝直发，提示走 ASSIST。"""
    a = Account(nickname="无官API号", stage="active", health_flag="green",
                stage_since=dt.date.today(), official_setup={})
    db.add(a)
    db.commit()
    t = Topic(title="官发", category="餐桌")
    db.add(t)
    db.commit()
    d = Draft(topic_id=t.id, title="x", body="b", status="approved",
              compliance={"S": [], "A": [], "B": []})
    db.add(d)
    db.commit()
    from app.models import PublishEvent
    ev = PublishEvent(content_id=d.id, account_id=a.id, result="pending")
    db.add(ev)
    db.commit()
    r = client.post(f"/api/dispatch/{ev.id}/official-publish")
    assert r.status_code == 400 and "ASSIST" in r.json()["detail"]


def test_aeo_overview_not_connected(client):
    r = client.get("/api/aeo/overview").json()
    assert r["connected"] is False  # 未配置 AEO 地址
    assert "hint" in r
