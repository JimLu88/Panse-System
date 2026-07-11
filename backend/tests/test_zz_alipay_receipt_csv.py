# -*- coding: utf-8 -*-
"""支付宝「电子客户回单」格式导入 (2026-07-11 治本): 主力号 App/网页导出的交易明细,
列头以「交易时间」开头、交易号列叫「交易订单号」。历史上这格式曾以无符号金额入库
致 140 笔支出记成正数(已按源表翻负修复); 本回归锁死: 由『收/支』定符号。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from decimal import Decimal as D

from app.models.finance import AlipayFlow
from app.services.alipay_import import import_alipay_csv

RECEIPT = """------------------------------------------------------------------------------------
导出信息：
姓名：测试人
支付宝账户：15800000000
起始时间：[2026-05-01 00:00:00]    终止时间：[2026-05-31 23:59:59]
共3笔记录

------------------------支付宝支付科技有限公司  电子客户回单------------------------
交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注,
2026-05-29 19:11:18,生活服务,万师傅,wan***@126.com,P202605291,支出,118.00,账户余额,交易成功,2026052922001422051456296641\t,2026052900001\t,,
2026-05-28 11:42:57,收钱码,雅雅,ya***@qq.com,收钱码收款,支出,109.99,账户余额,交易成功,2026052822001422051439081686\t,,,
2026-05-20 10:00:00,转账,某客户,ke***@qq.com,货款,收入,500.00,账户余额,交易成功,2026052022001422051400000001\t,,,
"""


def test_receipt_variant_signs(db_session):
    """回单变体: 支出→负 / 收入→正, 交易订单号入 transaction_no(去尾tab), 时间取「交易时间」。"""
    rep = import_alipay_csv(db_session, RECEIPT, account="主力号")
    assert not rep.errors
    rows = {f.transaction_no: f for f in db_session.query(AlipayFlow).all()}
    assert len(rows) == 3
    assert D(str(rows["2026052922001422051456296641"].amount)) == D("-118.00")
    assert D(str(rows["2026052822001422051439081686"].amount)) == D("-109.99")   # 收钱码收款也是支出
    assert D(str(rows["2026052022001422051400000001"].amount)) == D("500.00")
    w = rows["2026052922001422051456296641"]
    assert str(w.transaction_time)[:10] == "2026-05-29"
    assert w.transaction_type == "生活服务"


def test_old_personal_format_still_works(db_session):
    """老个人版(交易号开头)不受影响。"""
    OLD = """支付宝交易记录明细查询
账号:[test]
起始日期:[2026-05-01]
---------------------------------交易记录明细列表------------------------------------
交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,金额（元）,收/支,交易状态,备注,资金状态,
20260501123456789012345678,,2026-05-01 10:00:00,2026-05-01 10:00:00,,,即时到账交易,某人,测试,66.00,支出,交易成功,,已支出,
"""
    rep = import_alipay_csv(db_session, OLD, account="主力号")
    assert not rep.errors
    f = db_session.query(AlipayFlow).filter_by(transaction_no="20260501123456789012345678").one()
    assert D(str(f.amount)) == D("-66.00")