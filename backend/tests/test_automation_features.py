"""5项自动化功能测试:
1. 邮箱配置读写
2. CSV 模板下载 (各端点返回 200 + CSV 内容)
3. 账户余额导入预览
4. 支付宝流水导入预览
5. 月度报告推送 (scheduler job 空库时不崩溃)
"""
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.models.finance import AccountBalance
from app.services import email_import_service

client = TestClient(app)


# ----------------------------- 邮箱配置 ----------------------------- #

def test_email_config_read_defaults(db_session):
    cfg = email_import_service.get_config(db_session)
    assert cfg["host"] == ""
    assert cfg["folder"] == "INBOX"
    assert cfg["alipay_account"] == "企业号"
    assert cfg["password_set"] is False


def test_email_config_save_and_read(db_session):
    email_import_service.save_config(
        db_session,
        email_imap_host="imap.qq.com",
        email_imap_port="993",
        email_username="test@qq.com",
        email_folder="支付宝账单",
    )
    db_session.flush()
    cfg = email_import_service.get_config(db_session)
    assert cfg["host"] == "imap.qq.com"
    assert cfg["port"] == 993
    assert cfg["user"] == "test@qq.com"
    assert cfg["folder"] == "支付宝账单"


def test_email_poll_skips_when_unconfigured(db_session):
    """未配置 IMAP 时 poll_and_import 应立即返回而不出错。"""
    r = email_import_service.poll_and_import(db_session)
    assert r.scanned == 0
    assert r.imported == 0


# ----------------------------- CSV 模板下载 ----------------------------- #

def test_alipay_template_csv():
    resp = client.get("/api/finance/alipay-flows/template.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "交易流水号" in resp.text


def test_wanshifu_template_csv():
    resp = client.get("/api/finance/wanshifu-bills/template.csv")
    assert resp.status_code == 200
    assert "金额" in resp.text


def test_logistics_template_csv():
    resp = client.get("/api/finance/logistics-bills/template.csv")
    assert resp.status_code == 200
    assert "运费" in resp.text


def test_promotion_template_csv():
    resp = client.get("/api/finance/promotion-flows/template.csv")
    assert resp.status_code == 200
    assert "金额" in resp.text


def test_refill_records_template_csv():
    resp = client.get("/api/finance/refill-records/template.csv")
    assert resp.status_code == 200
    assert "订单号" in resp.text


def test_account_balances_template_csv():
    resp = client.get("/api/finance/accounts/template.csv")
    assert resp.status_code == 200
    assert "账户名" in resp.text and "期末余额" in resp.text


def test_aftersales_template_csv():
    resp = client.get("/api/aftersales/template.csv")
    assert resp.status_code == 200
    assert "订单号" in resp.text and "万师傅扣款" in resp.text


def test_qianniu_template_csv():
    resp = client.get("/api/orders/import-qianniu/template.csv")
    assert resp.status_code == 200
    assert "订单编号" in resp.text


# ----------------------------- 账户余额预览 ----------------------------- #

def test_parse_account_balances_preview(db_session):
    csv_text = "账户名,年,月,期末余额\n企业号,2026,4,5000\n,2026,4,100\n"
    from app.api.finance import _parse_account_balances_preview
    result = _parse_account_balances_preview(csv_text)
    assert result.total == 2
    assert result.valid_count == 1
    assert result.invalid_count == 1
    valid_row = next(r for r in result.preview_rows if r.valid)
    assert valid_row.data["account_name"] == "企业号"
    assert valid_row.data["closing_balance"] == "5000"


def test_parse_account_balances_preview_all_valid(db_session):
    csv_text = "账户名,年,月,期末余额\n企业号,2026,3,4000\n私账,2026,3,2000\n"
    from app.api.finance import _parse_account_balances_preview
    result = _parse_account_balances_preview(csv_text)
    assert result.valid_count == 2
    assert result.invalid_count == 0


# ----------------------------- 月度报告 job ----------------------------- #

def test_monthly_report_push_empty_db(db_session):
    """空库时月度报告 job 不应抛异常, 应返回 dict。"""
    from app.services.scheduler import _job_monthly_report_push
    result = _job_monthly_report_push(db_session)
    assert "revenue" in result
    assert "order_count" in result
    assert result["order_count"] == 0
