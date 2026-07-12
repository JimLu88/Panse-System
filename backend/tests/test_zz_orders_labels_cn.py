# -*- coding: utf-8 -*-
"""订单总表表头全中文 (2026-07-11 用户要求): Order 每一列都要有中文表头,
不允许再出现 est_packing / actual_logistics / parts_override 这类英文裸字段名。
覆盖机制: excel_schemas 优先 → _ENTITY_EXTRA_LABELS 补系统列 → _COMMON_LABELS 兜底。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
import re

from app.api.table_explorer import _build_label_map
from app.models.order import Order


def test_every_order_column_has_cn_label():
    """模型每列都有非裸英文的表头 (新增列漏配会在这里立刻红)。"""
    labels = _build_label_map("order")
    missing = [c.key for c in Order.__table__.columns
               if labels.get(c.key, c.key) == c.key and c.key != "id"]
    assert missing == [], f"以下订单列缺中文表头: {missing}"


def test_screenshot_columns_translated():
    """用户截图点名的列必须是中文。"""
    labels = _build_label_map("order")
    for key, expect_cn in [("est_packing", "预估打包费"), ("est_logistics", "预估物流费"),
                           ("actual_packing", "实际打包费"), ("actual_logistics", "实际物流费"),
                           ("est_install", "预估安装费"), ("actual_install", "实际安装费"),
                           ("actual_parts", "实际配件成本"), ("est_parts", "预估配件成本(定价表)"),
                           ("parts_override", "配件覆盖(逐单指定)"), ("custom_surcharge", "定制加价"),
                           ("wood_cost_est", "木作估算(定价表)")]:
        assert labels[key] == expect_cn


def test_schema_labels_still_win():
    """excel_schemas 定义的导入字段仍优先 (补充映射不覆盖)。"""
    labels = _build_label_map("order")
    assert labels["buyer_payable_amount"] == "买家应付金额"
    # 所有表头不含裸英文单词开头的 snake_case (粗检)
    for k, v in labels.items():
        assert not re.fullmatch(r"[a-z_]+", v), f"{k} 的表头仍是英文: {v}"