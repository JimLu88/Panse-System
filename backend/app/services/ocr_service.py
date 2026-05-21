"""供应商送货单 OCR (业务需求: 拍照自动入, 不手填).

调用 ai_provider 抽象, 用 OCR 配置 (后台可改)。
对每张图返回 ParsedDeliveryNote: 单号 + 日期 + 行项目 + 总金额 + 警告列表。

设计要点:
    - 让 AI 返回严格 JSON, 容错 Markdown 代码块包裹
    - 数字字段全部转 Decimal, 转不成的写进 warnings
    - 不抛 — 上游捕 OcrUnavailable / OcrParseError 后弹用户警告
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service
from app.services.ai_provider import AiUnavailable, build_provider


OCR_SYSTEM_PROMPT = """你是供应商送货单 OCR 助手, 把图片里的送货单解析成结构化 JSON。

供应商行业 (用于辅助判断单位/规格):
- 木作工厂 → 柜体板件, 规格通常如 "1800×850", 单位常见 "件 / 套 / 张"
- 岩板厂 → 大理石/岩板, 规格通常如 "1620×3290×10", 单位常见 "块 / 张 / 平方米"
- 玻璃厂 → 钢化玻璃, 规格通常如 "1828×858×6", 单位常见 "片 / 块"

必须严格按下面 JSON 结构返回, 不要任何解释文字, 不要 Markdown 代码块:
{
  "note_no": "字符串, 送货单编号 (单据右上角的号码)。识别不到则填 null",
  "delivery_date": "YYYY-MM-DD 或 null。优先用送货日期, 没有就用单据签发日",
  "lines": [
    {
      "item_name": "字符串, 商品/品名",
      "spec": "字符串, 规格尺寸如 1800×850 或 1620×3290×10, 没有填空串",
      "unit": "字符串, 单位 (件/张/块/片/套等)",
      "qty": 数字 (整数或小数),
      "unit_price": 数字 (元, 没标价填 null),
      "amount": 数字 (元, 没合计填 null),
      "raw_text": "字符串, 原图中这一行的完整文字 (用于复核)"
    }
  ],
  "total_amount": 数字 (元, 单据底部合计金额, 没识别到填 null),
  "warnings": ["字符串数组, 任何看不清/疑似有误/笔迹歪斜导致拿不准的提示"]
}

字段规范:
- 数字字段不要带千分位逗号, 不要带 ¥/元 符号
- 中文数字写法 (如 "壹仟贰佰") 转成阿拉伯数字
- 单据上常见的简写: "套餐" → spec 写 "套餐"
- 看不清的字段填空串 / null, 同时在 warnings 里说明
- 如果送货单上有多个表格, 所有行项目都合并进 lines"""


@dataclass
class ParsedDeliveryLine:
    line_no: int
    item_name: str
    spec: str
    unit: str
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    raw_text: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedDeliveryNote:
    note_no: Optional[str]
    delivery_date: Optional[date]
    total_amount: Optional[Decimal]
    lines: list[ParsedDeliveryLine]
    warnings: list[str]
    model: str
    raw_response: str
    confidence: Decimal  # 0-100 估算 (= 100 - 5*warnings)


class OcrUnavailable(RuntimeError):
    """OCR provider 未配置 / 调用失败."""


class OcrParseError(RuntimeError):
    """provider 返回了无法解析为期望结构的内容."""


_JSON_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    """容错: 去 markdown 围栏, 找到首个 { ... } 子串。"""
    cleaned = _JSON_CODE_FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # fallback: 取第一个 { 到最后一个 }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise OcrParseError(f"返回不是 JSON: {text[:200]}")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise OcrParseError(f"JSON 解析失败: {e}: {cleaned[start:end+1][:200]}") from e


def _to_decimal(v, *, warnings: list[str], label: str) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            return Decimal(str(v))
        except InvalidOperation:
            warnings.append(f"{label} 数字异常: {v}")
            return None
    if isinstance(v, str):
        s = v.replace(",", "").replace("¥", "").replace("元", "").strip()
        if not s:
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            warnings.append(f"{label} 不是数字: {v!r}")
            return None
    warnings.append(f"{label} 类型未知: {type(v).__name__}")
    return None


def _to_date(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return date(*[int(x) for x in v.replace("年", "-").replace("月", "-")
                          .replace("日", "").replace("/", "-").replace(".", "-").split("-")[:3]])
        except (ValueError, TypeError):
            continue
    return None


def parse_ocr_response(payload: dict, *, model: str, raw: str) -> ParsedDeliveryNote:
    warnings: list[str] = list(payload.get("warnings") or [])
    note_no = payload.get("note_no")
    if isinstance(note_no, (int, float)):
        note_no = str(note_no)

    parsed_date = _to_date(payload.get("delivery_date"))
    if payload.get("delivery_date") and not parsed_date:
        warnings.append(f"日期格式无法解析: {payload['delivery_date']!r}")

    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list):
        raise OcrParseError(f"lines 不是数组: {type(raw_lines).__name__}")

    parsed_lines: list[ParsedDeliveryLine] = []
    for i, ln in enumerate(raw_lines, start=1):
        if not isinstance(ln, dict):
            warnings.append(f"第 {i} 行不是对象, 跳过")
            continue
        line_warns: list[str] = []
        qty = _to_decimal(ln.get("qty"), warnings=line_warns, label="数量") or Decimal("0")
        unit_price = _to_decimal(ln.get("unit_price"), warnings=line_warns, label="单价")
        amount = _to_decimal(ln.get("amount"), warnings=line_warns, label="金额")
        # 若有单价+数量但没合计, 自动补
        if amount is None and unit_price is not None and qty:
            amount = (unit_price * qty).quantize(Decimal("0.01"))
        parsed_lines.append(ParsedDeliveryLine(
            line_no=i,
            item_name=str(ln.get("item_name") or "").strip(),
            spec=str(ln.get("spec") or "").strip(),
            unit=str(ln.get("unit") or "").strip(),
            qty=qty,
            unit_price=unit_price,
            amount=amount,
            raw_text=str(ln.get("raw_text") or "").strip(),
            warnings=line_warns,
        ))
        warnings.extend(f"行 {i}: {w}" for w in line_warns)

    total = _to_decimal(payload.get("total_amount"), warnings=warnings, label="合计金额")
    # 若给了行明细 + 没合计 → 用行金额求和
    if total is None and parsed_lines:
        s = sum((ln.amount for ln in parsed_lines if ln.amount is not None), Decimal("0"))
        if s > 0:
            total = s

    # confidence: 100 - 每条 warning 扣 5, 下限 30
    confidence = max(Decimal("30"), Decimal("100") - Decimal("5") * Decimal(len(warnings)))

    return ParsedDeliveryNote(
        note_no=note_no.strip() if isinstance(note_no, str) else None,
        delivery_date=parsed_date,
        total_amount=total,
        lines=parsed_lines,
        warnings=warnings,
        model=model,
        raw_response=raw,
        confidence=confidence,
    )


def ocr_delivery_note(
    db: Session,
    *,
    image_bytes: bytes,
    mime: str = "image/jpeg",
    supplier_name: str = "",
    supplier_type: str = "",
) -> ParsedDeliveryNote:
    """主入口: 给一张图, 返回结构化送货单。"""
    cfg = settings_service.get_ai_config(db, "ocr")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        raise OcrUnavailable(f"OCR 未配置: {e}") from e

    user_msg = (
        f"这是一张【{supplier_name or '供应商'}】"
        f"({supplier_type or '类型未知'}) 给我们送的送货单。"
        "请按系统提示的 JSON 格式返回。如果是手写歪歪扭扭的字, 拿不准的写进 warnings。"
    )
    try:
        resp = provider.chat_with_image(
            system=OCR_SYSTEM_PROMPT, user=user_msg,
            image_bytes=image_bytes, mime=mime, max_tokens=3000,
        )
    except AiUnavailable as e:
        raise OcrUnavailable(str(e)) from e

    payload = _extract_json(resp.text)
    return parse_ocr_response(payload, model=resp.model, raw=resp.text)
