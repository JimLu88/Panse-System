"""截图 OCR 服务 (Phase 3, 业务需求 1/6/12).

调用 AI 视觉模型解析:
    - 千牛后台订单截图  → 结构化订单 JSON (订单号 / SKU / 数量 / 客户 / 地址 / 备注 / 价格 / 实付 / 平台佣金 等)
    - 进货单截图       → 结构化采购 JSON (供应商 / 物料 / 数量 / 单价 / 金额)

设计:
    - 复用 ai_provider.chat_with_image (Anthropic / 通义 Qwen-VL 等)
    - 上传时支持单张图 + 批量 (一次 zip 多个截图)
    - AI 返回 JSON, 强制 2 段式: parse → preview → 用户确认 → confirm 入库
    - 不在此层入库, 由调用方 (api/orders_screenshot.py, api/purchase_screenshot.py) 完成

公开:
    parse_qianniu_order(db, image_bytes, mime) -> dict
    parse_purchase_invoice(db, image_bytes, mime) -> dict
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service
from app.services.ai_provider import AiUnavailable, build_provider

_logger = logging.getLogger("panse.vision_ocr")


_QIANNIU_SYSTEM = """你是淘宝/千牛后台订单截图解析助手。
从用户提供的订单截图中提取所有可见字段, 输出严格 JSON 数组 (每个截图可能有多个订单, 每个订单一个对象):
{
  "orders": [
    {
      "order_no": "订单编号",
      "platform": "淘宝/抖音/京东/拼多多 等",
      "order_date": "YYYY-MM-DD",
      "pay_time": "YYYY-MM-DD HH:MM",
      "customer_name": "买家名称",
      "customer_phone": "电话",
      "customer_address": "完整地址",
      "product_name": "商品名",
      "sku": "SKU 规格描述",
      "qty": 数字,
      "unit_price": 数字 (元),
      "discount": 数字 (优惠总额),
      "paid_amount": 数字 (买家实付),
      "platform_fee": 数字 (平台佣金, 可空),
      "freight": 数字 (运费, 可空),
      "remark": "买家留言/卖家备注",
      "extra_accessories": [
        {"name": "配件名称", "qty": 数字 (默认1), "note": "客户原话片段"}
      ],
      "confidence": 0.0-1.0 (整条订单的整体识别置信度),
      "warnings": ["识别不清的字段名"]
    }
  ],
  "ocr_warnings": ["全局识别问题, 如截图模糊"]
}

规则:
- 数字字段返回纯数字 (不带¥/元), 不确定就 null
- 日期统一 YYYY-MM-DD
- 看不清的字段一律 null + 加入 warnings
- extra_accessories: 仔细读买家留言/备注, 若提到要"加""多配""额外""赠送"等标准 SKU 之外的
  配件(如 加2个抱枕、多配一块玻璃、送脚垫), 逐项列出; 备注没提配件就返回空数组 []
- 仅输出 JSON, 不要任何解释文字"""


_PURCHASE_SYSTEM = """你是采购入库单截图解析助手。
从用户提供的采购单/进货单截图中提取信息, 输出严格 JSON:
{
  "purchase": {
    "supplier_name": "供应商名称",
    "purchase_date": "YYYY-MM-DD",
    "purchase_no": "单号 (如有)",
    "tracking_no": "快递单号 (如可见)",
    "carrier": "快递公司 (如可见)",
    "lines": [
      {
        "material_name": "物料/商品名",
        "spec": "规格",
        "qty": 数字,
        "unit": "单位",
        "unit_price": 数字,
        "amount": 数字
      }
    ],
    "freight": 数字,
    "total_amount": 数字,
    "remark": "备注",
    "warnings": ["识别问题"]
  }
}

规则:
- 数字字段返回纯数字; 不确定 null
- 缺字段填 null, 不要造数据
- 仅输出 JSON"""


_FACTORY_RECON_SYSTEM = """你是工厂对账单截图解析助手。
从用户提供的工厂对账单截图 (可能是表格) 中提取每一行对账记录, 输出严格 JSON:
{
  "rows": [
    {
      "factory_name": "工厂名称",
      "period_start": "YYYY-MM-DD (账期起, 可空)",
      "period_end": "YYYY-MM-DD (账期止, 可空)",
      "order_amount": 数字 (本期下单金额, 可空),
      "bill_amount": 数字 (工厂账单金额, 可空),
      "paid_amount": 数字 (实际已支付, 可空),
      "alipay_flow_no": "支付宝流水号 (如可见)",
      "remark": "备注",
      "warnings": ["识别不清的字段名"]
    }
  ],
  "ocr_warnings": ["全局识别问题, 如截图模糊"]
}

规则:
- 数字字段返回纯数字 (不带¥/元), 不确定就 null
- 日期统一 YYYY-MM-DD
- 一张对账单可能有多行 (多个工厂或多个账期), 每行一个对象
- 工厂名称是必须的, 实在看不清填 "未知工厂" 并加 warnings
- 仅输出 JSON, 不要任何解释文字"""


_ALIPAY_SYSTEM = """你是支付宝流水截图解析助手。
从用户提供的支付宝账单/流水截图中提取每一笔交易, 输出严格 JSON:
{
  "flows": [
    {
      "transaction_time": "YYYY-MM-DD HH:MM:SS (或 YYYY-MM-DD, 可空)",
      "transaction_no": "交易流水号/订单号 (尽量取支付宝交易号)",
      "transaction_type": "交易类型/分类 (如 在线支付/转账/退款, 可空)",
      "counterparty": "交易对方 (可空)",
      "amount": 数字 (收支金额, 收入为正/支出为负; 截图里有'收入/支出'或+/-就按符号),
      "related_order_no": "关联商户订单号 (可空)",
      "balance": 数字 (账户余额, 可空),
      "remark": "备注/商品说明 (可空)"
    }
  ],
  "ocr_warnings": ["全局识别问题, 如截图模糊/被截断/可能漏行"]
}

规则:
- 金额返回纯数字带符号 (支出为负数), 不带 ¥/元; 不确定就 null
- 交易流水号必须尽量取全; 取不到就 null 并加 warnings (无流水号的行无法入库)
- 截图通常是长账单, 若怀疑上下被截断务必在 ocr_warnings 提示"可能漏行, 建议用 CSV 导出"
- 仅输出 JSON, 不要任何解释文字"""


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"AI 返回不是 JSON: {text[:300]}")
        return json.loads(cleaned[start: end + 1])


def parse_qianniu_order(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析千牛订单截图. 返回 {"orders": [...], "ocr_warnings": [...]}."""
    cfg = settings_service.get_ai_config(db, "ocr")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        raise AiUnavailable(f"OCR 未配置, 请到管理 → AI 集成 配 vision 模型: {e}")

    resp = provider.chat_with_image(
        system=_QIANNIU_SYSTEM,
        user="请解析这张千牛订单截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        raise AiUnavailable(f"AI 返回无法解析: {e}")
    # 规范化
    data.setdefault("orders", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["orders"], list):
        data["orders"] = []
    return data


def parse_purchase_invoice(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析采购单/进货单截图. 返回 {"purchase": {...}}."""
    cfg = settings_service.get_ai_config(db, "ocr")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        raise AiUnavailable(f"OCR 未配置: {e}")
    resp = provider.chat_with_image(
        system=_PURCHASE_SYSTEM,
        user="请解析这张采购/进货单截图.",
        image_bytes=image_bytes, mime=mime, max_tokens=3000,
    )
    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        raise AiUnavailable(f"AI 返回无法解析: {e}")
    data.setdefault("purchase", {})
    if isinstance(data["purchase"], dict):
        data["purchase"].setdefault("lines", [])
        data["purchase"].setdefault("warnings", [])
    return data


def parse_factory_reconciliation(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析工厂对账单截图. 返回 {"rows": [...], "ocr_warnings": [...]}."""
    cfg = settings_service.get_ai_config(db, "ocr")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        raise AiUnavailable(f"OCR 未配置, 请到管理 → AI 集成 配 vision 模型: {e}")
    resp = provider.chat_with_image(
        system=_FACTORY_RECON_SYSTEM,
        user="请解析这张工厂对账单截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        raise AiUnavailable(f"AI 返回无法解析: {e}")
    data.setdefault("rows", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["rows"], list):
        data["rows"] = []
    return data


def parse_alipay_flow_screenshot(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析支付宝流水截图. 返回 {"flows": [...], "ocr_warnings": [...]}.

    flows 内字段直接对应 alipay_import.import_alipay_rows 的 payload 键
    (transaction_no/transaction_time/transaction_type/counterparty/amount/
     related_order_no/balance/remark)。
    """
    cfg = settings_service.get_ai_config(db, "ocr")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        raise AiUnavailable(f"OCR 未配置, 请到管理 → AI 集成 配 vision 模型: {e}")
    resp = provider.chat_with_image(
        system=_ALIPAY_SYSTEM,
        user="请解析这张支付宝流水截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        raise AiUnavailable(f"AI 返回无法解析: {e}")
    data.setdefault("flows", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["flows"], list):
        data["flows"] = []
    return data
