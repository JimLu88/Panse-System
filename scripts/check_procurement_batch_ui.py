"""Headless, mock-only procurement UI check. Never connects to ERP/accounts.

Run through webapp-testing with_server.py and a local Vite preview on port 5187.
All API requests are fulfilled in memory; all non-local requests are blocked.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    out = Path(tempfile.mkdtemp(prefix="procurement-p1-ui-"))
    task = {
        "id": 1, "task_no": "OFFLINE-ONLY-1", "title": "离线合成需求",
        "category": "production", "item_name": "合成配件", "specification": "测试规格",
        "quantity": 2, "unit": "件", "target_unit_price": None,
        "requirements": "离线测试，不向商家发送", "search_queries": [],
        "execution_mode": "assisted", "taobao_client_mode": "desktop",
        "channels": ["taobao"], "channel_daily_limits": {"taobao": 10},
        "followup_intervals_hours": {"taobao": 12}, "planned_merchant_count": 50,
        "max_followup_rounds": 1, "ab_test_enabled": False, "ab_test_sample_size": 0,
        "script_a": "合成文案", "script_b": None, "scripts_reviewed_at": "2026-09-26T01:00:00Z",
        "scripts_reviewed_by": "offline-test", "status": "ready", "created_by": "offline-test",
        "batch_policy_version": "48h-v1", "started_at": None, "deadline_at": None,
        "closed_at": None, "deadline_report": None,
        "created_at": "2026-09-26T01:00:00Z", "updated_at": "2026-09-26T01:00:00Z",
        "counts": {"total": 50, "discovery_pending": 0, "candidates": 50,
                   "sent": 0, "replied": 0, "needs_manual": 0, "completed": 0},
    }
    requests, errors, blocked = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, timezone_id="UTC")
        context.add_init_script("localStorage.setItem('panse_token', 'offline-ui-fixture-only')")

        def route_request(route):
            req = route.request
            url = urlsplit(req.url)
            if url.hostname != "127.0.0.1" or url.port != 5187:
                blocked.append(req.url)
                route.abort()
                return
            if not url.path.startswith("/api/"):
                route.continue_()
                return
            requests.append({"method": req.method, "path": url.path, "body": req.post_data_json if req.post_data else None})
            if url.path == "/api/auth/me":
                body = {"id": 1, "username": "offline-test", "display_name": "离线测试",
                        "role": "admin", "is_active": True, "page_perms": None}
            elif url.path == "/api/procurement/tasks":
                if req.method == "POST":
                    task.update(req.post_data_json)
                    task.update(status="draft", scripts_reviewed_at=None, ab_test_sample_size=0)
                    body = task
                else:
                    body = [task]
            elif url.path == "/api/procurement/tasks/1/activate":
                task.update(status="running", started_at="2026-09-26T02:00:00Z", deadline_at="2026-09-28T02:00:00Z")
                body = task
            elif url.path == "/api/procurement/agent-status":
                body = {"token_configured": False, "agents": [], "active_leases": 0}
            elif url.path == "/api/procurement/summary/daily":
                body = {"date": "2026-09-26"}
            else:
                body = []
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

        context.route("**/*", route_request)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def open_workspace():
            page.goto("http://127.0.0.1:5187/purchases")
            page.wait_for_load_state("networkidle")
            page.get_by_text("智能询价", exact=True).click()
            page.wait_for_load_state("networkidle")

        try:
            open_workspace()
            if args.inspect:
                print(page.locator("body").inner_text())
                print("REQUESTS", json.dumps(requests, ensure_ascii=False))
                print("ERRORS", errors)
                return
            page.get_by_role("button", name="打开工作台").click()
            expect(page.get_by_text("尚未启动，不计时。", exact=False)).to_be_visible()
            page.get_by_role("button", name="启动 48 小时计时").click()
            expect(page.get_by_text("启动后冻结当前需求，统一计时 48 小时；不包含下单和付款。", exact=True)).to_be_visible()
            page.get_by_role("button", name=re.compile(r"^确\s*定$")).click()
            expect(page.get_by_text(re.compile(r"截止：2026/9/28 10:00:00"))).to_be_visible()
            expect(page.get_by_role("button", name="启动 48 小时计时")).to_have_count(0)
            page.screenshot(path=str(out / "active-batch.png"), full_page=True, animations="disabled")

            task.update(status="expired", closed_at="2026-09-28T02:00:30Z", deadline_report={
                "kind": "cutoff_evidence_summary", "as_of": task["deadline_at"],
                "generated_at": "2026-09-28T02:00:30Z", "confirmed_sent_merchants": 2,
                "replied_merchants": 1, "not_confirmed_sent_merchants": 48, "recommendations_ready": False,
            })
            open_workspace()
            page.get_by_role("button", name="打开工作台").click()
            expect(page.get_by_text("截止证据快照已生成（不是最终推荐）", exact=True)).to_be_visible()
            expect(page.get_by_text("未确认不等于未发送，不可据此补发。", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="保存搜索词", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="明确确认话术", exact=True)).to_be_disabled()
            page.screenshot(path=str(out / "cutoff-report.png"), full_page=True, animations="disabled")

            open_workspace()
            page.get_by_role("button", name="新建采购询价").click()
            dialog = page.get_by_role("dialog", name="新建采购询价计划", exact=True)
            count = dialog.get_by_label("计划询问商家数量", exact=True)
            ab = dialog.get_by_role("switch")
            expect(ab).to_have_attribute("aria-checked", "false")
            for n in (10, 20, 30, 50):
                dialog.get_by_role("button", name=f"{n} 家", exact=True).click()
                expect(count).to_have_value(str(n))
            count.fill("17")
            count.press("Tab")
            expect(count).to_have_value("17")
            count.fill("1")
            count.press("Tab")
            expect(ab).to_be_disabled()
            expect(ab).to_have_attribute("aria-checked", "false")
            dialog.get_by_role("button", name="50 家", exact=True).click()
            dialog.get_by_label("本次计划名称", exact=True).fill("离线页面验收")
            dialog.get_by_label("采购品名", exact=True).fill("合成配件")
            dialog.get_by_label("规格", exact=True).fill("测试规格")
            dialog.get_by_label("本次特别要求", exact=True).fill("测试资料，不外发")
            expect(dialog.get_by_text("保存需求不开始计时。", exact=False)).to_be_visible()
            page.screenshot(path=str(out / "selectable-count.png"), full_page=True, animations="disabled")
            task.update(started_at=None, deadline_at=None, closed_at=None, deadline_report=None)
            dialog.get_by_role("button", name="建立任务并生成话术").click()
            expect(dialog).not_to_be_visible()
            created = [r for r in requests if r["method"] == "POST" and r["path"] == "/api/procurement/tasks"]
            assert len(created) == 1 and created[0]["body"]["planned_merchant_count"] == 50
            assert created[0]["body"]["ab_test_enabled"] is False
            assert task["started_at"] is None and task["deadline_at"] is None
            assert len([r for r in requests if r["path"].endswith("/activate")]) == 1
            assert not errors, errors
            assert not blocked, blocked
            print("PASS: mock-only UI; 1/17/10/20/30/50 choices; AB off; draft no clock; explicit start; Beijing deadline; cutoff warning")
            print("screenshots=" + str(out))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
