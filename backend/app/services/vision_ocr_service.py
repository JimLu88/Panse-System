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
- purchase_date: 采购日期常在单据【表头/标题】处(可能写作「3.26」「3月26日」「2026.3.26」),
  务必识别; 只有月日无年份时保留「月.日」原样(如 "3.26") 或补全为 YYYY-MM-DD 都可, 系统会解析
- 每行 amount 是【该行】金额(单价×数量), total_amount 是整单合计 —— 不要把整单合计填进每行
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


def _ocr_image_resp(db: Session, *, system: str, user: str, image_bytes: bytes,
                    mime: str, max_tokens: int):
    """视觉 OCR 调用 + 主→兜底自动切换 (用户拍板 2026-06-12: 怕的不是钱是自动化断档)。
    主用 'ocr' 配置(如 gpt-5.4-mini); 额度用光/报错/超时 → 自动切 'ocr_fallback'(如本机 Ollama),
    永不因主用挂掉退回人工。两者都不可用才抛 AiUnavailable。"""
    from datetime import date
    today = date.today().isoformat()
    # 以天为时限 (用户拍板 2026-06-12): 主用今天已确认挂 → 当天直接走本地兜底, 不再反复试主用;
    # 隔天 (down != today) 自动先试主用 gpt-5.4-mini, 它额度/通道恢复就自愈。
    down = settings_service.get(db, "ocr_primary_down_day", env_fallback=False)
    kinds = ("ocr_fallback",) if down == today else ("ocr", "ocr_fallback")
    errors = []
    for kind in kinds:
        cfg = settings_service.get_ai_config(db, kind)
        if kind == "ocr_fallback" and not (cfg.get("base_url") or cfg.get("model")):
            continue   # 兜底槽位未配 → 跳过
        try:
            provider = build_provider(cfg)
            return provider.chat_with_image(
                system=system, user=user, image_bytes=image_bytes, mime=mime, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — 主用失败自动转兜底
            errors.append(f"{kind}: {type(e).__name__}")
            if kind == "ocr":
                _logger.warning("OCR 主用今日失败, 当天改走本地兜底, 明日再先试主用: %s", e)
                try:
                    settings_service.set_value(db, "ocr_primary_down_day", today)
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
            continue
    raise AiUnavailable(f"OCR 主用+兜底均不可用 ({'; '.join(errors)})")


def _ocr_json(db: Session, *, system: str, user: str, image_bytes: bytes,
              mime: str, max_tokens: int) -> dict:
    """OCR 调用 + JSON 解析, 坏 JSON 自动带错重调一次再抛 (2026-07-13)。

    案发: 打包手写本整页 17+ 行, OCR 槽当时主/兜底都是本机 qwen2.5vl:7b 小模型,
    长 JSON 中途吐坏(缺冒号) → 'AI 返回无法解析: Expecting : delimiter' 直接打断人工流程。
    一次格式坏不该终结整次识别: 把语法错误喂回去让模型重出一次, 重试仍坏才抛。"""
    resp = _ocr_image_resp(db, system=system, user=user,
                           image_bytes=image_bytes, mime=mime, max_tokens=max_tokens)
    try:
        return _extract_json(resp.text)
    except ValueError as e:
        _logger.warning("OCR 返回坏 JSON, 自动重调一次: %s", e)
        retry_user = (f"{user}\n\n注意: 你上次输出的 JSON 有语法错误({e}); "
                      "请重新输出【完整且合法】的 JSON, 只输出 JSON 本体, 不要任何解释文字。")
        resp2 = _ocr_image_resp(db, system=system, user=retry_user,
                                image_bytes=image_bytes, mime=mime, max_tokens=max_tokens)
        try:
            return _extract_json(resp2.text)
        except ValueError as e2:
            raise AiUnavailable(f"AI 返回无法解析(重试一次后仍坏): {e2}")


def parse_qianniu_order(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析千牛订单截图. 返回 {"orders": [...], "ocr_warnings": [...]}."""
    data = _ocr_json(
        db, system=_QIANNIU_SYSTEM, user="请解析这张千牛订单截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    # 规范化
    data.setdefault("orders", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["orders"], list):
        data["orders"] = []
    return data


_BALANCE_SYSTEM = (
    "你是财务OCR助手, 读电商资金截图的余额。同一张图常有多个并排板块(聚合结算账户/保证金账户/万相台无界版/"
    "支付宝企业账户/支付宝账单…), 只取与「账户」名匹配的那个板块里的数, 其余板块一律无视。\n"
    "按给定的「账户」名对号入座:\n"
    "  · 含『聚合/资金/淘宝聚合』→ 『聚合结算账户』板块最上方最大的「账户余额」(不是可提现/保证金/冻结);\n"
    "  · 含『推广/万相台』→ 『万相台无界版』板块里的「账户总余额」那个数(该板块只有『账户总余额』和『优惠券金额』"
    "两个数, 取账户总余额, 如 3,047.73), 把它填进 available; 找到这个板块就是 high;\n"
    "  · 含『保证金』→ 保证金账户「可用余额」;\n"
    "  · 含『支付宝企业』→ 『支付宝企业账户』板块的「可用余额」;\n"
    "  · 含『支付宝主力/主力』→ 个人支付宝, 走专用 _BALANCE_MAIN_SYSTEM(不在本多板块规则内);\n"
    "  · 含『万师傅』→ 右上角账户余额(常为 0);\n"
    "  · 其余 → 页面最显著的主账户余额。\n"
    "只返回 JSON: {\"available\": 数字或null, \"label_found\": \"实际读的板块+余额项\", "
    "\"confidence\": \"high|low\", \"note\": \"简述\"}\n"
    "数字去掉逗号和¥符号, 保留两位小数。找到匹配板块且数清晰 → high; 找不到匹配板块/有歧义/看不清 → available=null, confidence=low。\n"
    "绝不编造、绝不拿别的板块的数冒充; 完全读不到就 available=null。"
)

# 支付宝【主力号=个人号】专用: 页面是个人支付宝『交易记录』页(consumeprod.alipay.com), 单一余额,
# 不是并排板块结构 → 用多板块 _BALANCE_SYSTEM 会因"找不到叫主力号的板块"而误判 null。故单开简单提示词。
# (2026-07-10: 主力号余额抓图从 b.alipay 企业平台改回个人网址后配套, 见 Web-Agent bal_alipay_main)
_BALANCE_MAIN_SYSTEM = (
    "你是财务OCR助手, 读【个人支付宝·交易记录页】截图里的账户可用余额。\n"
    "页面顶部是『你好, 某某』, 中部『交易记录』标题右侧有一行绿色的『可用余额 X 元』——X 就是要读的可用余额。\n"
    "⚠页面下方那张交易流水明细表里每一笔带 +/- 号的金额(如 +8000.00 / -67.99 / -150.00)都是交易流水, 不是余额, 绝不能取。\n"
    "只返回 JSON: {\"available\": 数字或null, \"label_found\": \"实际读到的余额项\", "
    "\"confidence\": \"high|low\", \"note\": \"简述\"}\n"
    "数字去掉逗号和¥符号、保留两位小数。读到『可用余额』后的那个数 → confidence=high; "
    "整页压根没有『可用余额』三个字(例如落到了登录页) → available=null、confidence=low。绝不编造。"
)


def parse_balance_screenshot(
    db: Session, image_bytes: bytes, *, mime: str = "image/png", account_hint: str = "",
) -> dict:
    """读余额截图的「可用余额」. 返回 {available, label_found, confidence, note}.
    调用方据 confidence/available 决定是否写库 (读不准不写, 报异常)。"""
    # 主力号=个人支付宝, 页面是单一余额的『交易记录』页 → 用专用简单提示词;
    # 多板块 _BALANCE_SYSTEM 会因"找不到叫主力号的板块"误判 null(2026-07-10 实测)。
    if "主力" in (account_hint or ""):
        system = _BALANCE_MAIN_SYSTEM
        user = f"账户: {account_hint or '主力号'}。读这张个人支付宝交易记录页的可用余额, 输出 JSON."
    else:
        system = _BALANCE_SYSTEM
        user = f"账户: {account_hint or '未知'}。按上面规则读这张截图里该账户对应板块的余额, 输出 JSON."
    data = _ocr_json(
        db, system=system, user=user,
        image_bytes=image_bytes, mime=mime, max_tokens=500,
    )
    # 推广余额反向校验 (2026-07-09): 06-29 实测 OCR 把千牛资金页最上方『聚合结算账户』57855.45
    # 误读成 57.85 记成推广余额。推广只认『万相台无界版·账户总余额』; 若读到的板块是聚合结算/保证金/
    # 可提现/冻结 → 强制判低置信, 调用方就不写库(报异常待人工), 免得又把别的账户当推广余额落库。
    _hint = account_hint or ""
    if "推广" in _hint or "万相台" in _hint:
        _label = str(data.get("label_found") or "")
        if any(k in _label for k in ("聚合", "保证金", "可提现", "冻结")):
            data["confidence"] = "low"
            data["note"] = (str(data.get("note") or "") +
                            f" | 反向校验: 推广余额只认万相台无界版, 却读到「{_label}」→ 判低置信不入库")
    return data


def parse_purchase_invoice(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析采购单/进货单截图. 返回 {"purchase": {...}}."""
    data = _ocr_json(
        db, system=_PURCHASE_SYSTEM, user="请解析这张采购/进货单截图.",
        image_bytes=image_bytes, mime=mime, max_tokens=3000,
    )
    data.setdefault("purchase", {})
    if isinstance(data["purchase"], dict):
        data["purchase"].setdefault("lines", [])
        data["purchase"].setdefault("warnings", [])
    return data


def parse_factory_reconciliation(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析工厂对账单截图. 返回 {"rows": [...], "ocr_warnings": [...]}."""
    data = _ocr_json(
        db, system=_FACTORY_RECON_SYSTEM, user="请解析这张工厂对账单截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    data.setdefault("rows", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["rows"], list):
        data["rows"] = []
    return data


_PROMO_SIGNUP_SYSTEM = """\
你是电商活动报名结果截图解析器。截图来自淘宝/天猫营销中心或小红书商家后台的活动报名页。
请提取每一行报名记录, 输出严格 JSON (不要 markdown 代码块):
{
  "rows": [
    {"sku_code": "SKU编码或商品编码 (尽量提取, 没有则用商品名)",
     "channel": "taobao 或 xhs (按截图来源平台判断)",
     "campaign_name": "活动名称 (如 618/双11/单品宝)",
     "signup_price": 1234.56},
    ...
  ],
  "ocr_warnings": ["看不清/不确定的内容写在这里"]
}
注意: signup_price 是数字不带货币符号; 模糊或被遮挡的行放进 ocr_warnings 而不是猜。
"""


def parse_promo_signup(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """Plan F1: 解析活动报名结果截图. 返回 {"rows": [...], "ocr_warnings": [...]}."""
    data = _ocr_json(
        db, system=_PROMO_SIGNUP_SYSTEM, user="请解析这张活动报名结果截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    data.setdefault("rows", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["rows"], list):
        data["rows"] = []
    return data


_PACKING_BILL_SYSTEM = """你是「打包费手写账单」识别助手。图片是打包工人手写的笔记本/便签照片,逐行记录每单的打包费。
请逐行提取, 输出严格 JSON (不要 markdown 代码块):
{
  "rows": [
    {
      "row_date": "YYYY-MM-DD 或 null (常只写几号, 如'5号'→当月; 看不清 null)",
      "customer_name": "客户/收货人姓名 (手写, 尽量识别)",
      "order_no": "订单号 (本子上很少写, 没有就 null)",
      "product": "产品/款式简述 (可空)",
      "packing_fee": 数字 (这单的打包费金额, 元, 不带符号),
      "excluded": true/false (这行是否被标注为「不算/不计入/改客户/非本店/作废/划掉」等 → true),
      "exclude_reason": "若 excluded=true, 写本子上的原话 (如'改客户'/'退了')",
      "note": "其它备注原话",
      "confidence": 0.0-1.0 (这一行手写识别的把握),
      "warnings": ["这行里看不清的字段, 尤其姓名/金额"]
    }
  ],
  "declared_total": 数字或 null (本子上若写了「合计/总计 XXX 元」, 填那个数, 用来和各行相加互核),
  "ocr_warnings": ["全局问题, 如字迹潦草/被遮挡/可能漏行"]
}

规则:
- 这是手写体, 中文姓名极易认错 — 没把握的姓名照样填但 confidence 调低并写进 warnings, 绝不编造。
- 金额只填纯数字; 划掉/涂改的行 excluded=true 并尽量读出原值。
- 「改客户」「不是我们的」「作废」「不算」「退了」等批注 → excluded=true。
- 只输出 JSON, 不要解释。"""


def parse_packing_bill(
    db: Session, image_bytes: bytes, *, mime: str = "image/jpeg",
) -> dict:
    """解析打包费手写账单照片. 返回 {"rows": [...], "declared_total": ..., "ocr_warnings": [...]}.

    手写中文姓名识别准确率有限 (~60-80%), 故走 parse→预览→人工复核→commit, 不无人值守入库。
    """
    data = _ocr_json(
        db, system=_PACKING_BILL_SYSTEM, user="请逐行识别这张手写打包费账单, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    data.setdefault("rows", [])
    data.setdefault("ocr_warnings", [])
    data.setdefault("declared_total", None)
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
    data = _ocr_json(
        db, system=_ALIPAY_SYSTEM, user="请解析这张支付宝流水截图, 输出 JSON.",
        image_bytes=image_bytes, mime=mime, max_tokens=4000,
    )
    data.setdefault("flows", [])
    data.setdefault("ocr_warnings", [])
    if not isinstance(data["flows"], list):
        data["flows"] = []
    return data
