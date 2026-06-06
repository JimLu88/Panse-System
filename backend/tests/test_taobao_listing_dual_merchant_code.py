"""淘宝导出有两个「商家编码」列: 第1个=14位产品编码, 第2个=16位SKU编码。
验证 import_listings 取第2个作为 sku_code, 第1个作为 product_code, 并能匹配系统SKU。"""
from __future__ import annotations

import io

import openpyxl

from app.models.pricing import PricingSku
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_listing_service


def _export_bytes(rows: list[list], two_codes: bool = True) -> bytes:
    """构造一份淘宝商品导出 .xlsx (表头第3行, 含 1 或 2 个商家编码列)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["淘宝商品导出"])                      # 行1: 标题
    ws.append(["商品信息", "", "SKU信息"])           # 行2: 分组标题
    if two_codes:
        header = ["商品Id", "宝贝标题", "商家编码", "销售属性", "skuId",
                  "价格(元)", "库存(件)", "商家编码"]   # 第2个商家编码 = SKU级
    else:
        header = ["商品Id", "宝贝标题", "商家编码", "销售属性", "skuId",
                  "价格(元)", "库存(件)"]
    ws.append(header)                                 # 行3: 真表头
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_second_merchant_code_used_as_sku_code(db_session):
    # Arrange: 系统已有该 16 位 SKU
    db_session.add(PricingSku(
        product_code="PPS26380040225", sku="榉木床头柜-标准",
        sku_code="PPS2638004022511",
    ))
    db_session.commit()
    data = _export_bytes([[
        "1036312802226", "畔色榉木床头柜",
        "PPS26380040225",                                  # 第1个商家编码=14位产品编码
        "安装方式:免安装;颜色分类:榉木床头柜-标准;",          # 销售属性(多属性)
        "6218230362901", "880", "10",
        "PPS2638004022511",                                # 第2个商家编码=16位SKU编码
    ]])

    # Act
    result = taobao_listing_service.import_listings(db_session, data)

    # Assert
    assert result["total"] == 1
    assert result["matched"] == 1
    row = db_session.query(TaobaoListing).one()
    assert row.merchant_code == "PPS26380040225"      # 第1个 -> 14位
    assert row.sku_code == "PPS2638004022511"          # 第2个 -> 16位 SKU编码
    assert row.product_code == "PPS26380040225"        # 经 sku_index 反查
    assert row.matched is True
    assert row.taobao_sku_id == "6218230362901"


def test_parse_rows_exposes_sku_code_raw():
    data = _export_bytes([[
        "1036312802226", "床头柜", "PPS26380040225", "颜色分类:标准;",
        "6218230362901", "880", "10", "PPS2638004022511",
    ]])
    records, warnings = taobao_listing_service.parse_rows(data)
    assert records and records[0]["sku_code_raw"] == "PPS2638004022511"


def test_import_sets_shop_field(db_session):
    # shop 显式传入 -> 写到每条 listing (分店统计用)
    data = _export_bytes([[
        "1036312802226", "床头柜", "PPS26380040225", "颜色分类:榉木床头柜-标准;",
        "6218230362901", "880", "10", "PPS2638004022511",
    ]])
    taobao_listing_service.import_listings(db_session, data, shop="畔色店")
    row = db_session.query(TaobaoListing).one()
    assert row.shop == "畔色店"


def test_reimport_without_shop_keeps_existing(db_session):
    # 重导不带 shop 时, 不应清空已有 shop
    data = _export_bytes([[
        "1036312802226", "床头柜", "PPS26380040225", "颜色分类:榉木床头柜-标准;",
        "6218230362901", "880", "10", "PPS2638004022511",
    ]])
    taobao_listing_service.import_listings(db_session, data, shop="畔色店")
    taobao_listing_service.import_listings(db_session, data)  # 无 shop 重导
    row = db_session.query(TaobaoListing).one()
    assert row.shop == "畔色店"


def test_single_merchant_code_falls_back_to_product_code(db_session):
    # 只有1个商家编码(14位产品编码), 系统有该 product_code -> product_code 命中, 不崩
    db_session.add(PricingSku(
        product_code="PPS26380040225", sku="榉木床头柜-标准",
        sku_code="PPS2638004022511",
    ))
    db_session.commit()
    data = _export_bytes([[
        "1036312802226", "床头柜", "PPS26380040225",
        "颜色分类:榉木床头柜-标准;", "6218230362901", "880", "10",
    ]], two_codes=False)

    result = taobao_listing_service.import_listings(db_session, data)
    assert result["total"] == 1
    row = db_session.query(TaobaoListing).one()
    assert row.merchant_code == "PPS26380040225"
    assert row.product_code == "PPS26380040225"
    # 16位列不存在 -> sku_code 为空, 但不报错
    assert row.sku_code is None
