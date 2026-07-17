"""活动核对器·2026-07-17 真实导出回归验收 (spec §八: 135一分不差+126贴线+0报警 复现)。

样本 (本机存在才跑, 缺任一自动 skip):
- 活动商品导出: 88VIP 大促第二场「7月超级88」当天 17:31 千牛原样导出 (354 个已发布 SKU 行,
  合并单元格续行格式 → 锁 parse_activity_items_export 的前向填充)
- 价格映射: 当天 ERP 定价 dump (===JSON_BEGIN=== 包 JSON, 705 SKU)
  + 三个当天晚些时候才轮换补映射的 sid (dump 里没有, 人工核对时手工对上的)
- 单品立减导出: 「单品立减0717」40 行, 档期 2026-07-17 20:00:00 ~ 2026-07-19 23:59:59

口径 = 2026-07-17 人工核对: 目标到手 = big_buyer (88VIP 大促 12% 场);
一分不差 135 / 贴线 126 (让幅全部 ≤0.9) / >2元报警 0;
占位 90、无映射 1、J未刷新 2 允许存在但绝不许出报警。
"""
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from app.services import campaign_recon_service as crs

_ACTIVITY_XLSX = (r"C:\Users\lzdwy\Desktop"
                  r"\「2026年7月淘宝平台大促第二场7月超级88」活动商品导出20260717173115.xlsx")
_DISCOUNT_XLSX = r"C:\Users\lzdwy\Desktop\单品立减_140496309164_202607171737.xlsx"
_PRICING_DUMP = os.path.join(os.path.dirname(__file__), "fixtures", "pricing_dump_20260717.txt")

# 三个后补映射 (2026-07-17 晚轮换的新 sid, dump 里还没有): sku_code → taobao_sku_id
_LATE_REMAPS = {
    "PPS2633008032212": "6280283835626",
    "PPS2633008032216": "6280283835627",
    "PPS2425007090122": "6280310355149",
}

_HAVE_SAMPLES = all(os.path.exists(p) for p in (_ACTIVITY_XLSX, _DISCOUNT_XLSX, _PRICING_DUMP))

pytestmark = pytest.mark.skipif(
    not _HAVE_SAMPLES, reason="2026-07-17 真实导出样本不在本机 (桌面两份导出 + 定价 dump)")


def _load_price_map() -> dict:
    raw = Path(_PRICING_DUMP).read_text(encoding="utf-8")
    payload = raw.split("===JSON_BEGIN===")[1].split("===JSON_END===")[0]
    rows = json.loads(payload)["rows"]
    by_code = {r["sku_code"]: r for r in rows}
    pm: dict = {}
    for r in rows:
        for sid in [r.get("sku_id"), *(r.get("alt_sku_ids") or [])]:
            if sid:
                pm[str(sid)] = r
    for code, sid in _LATE_REMAPS.items():
        pm[sid] = by_code[code]
    return pm


def test_acceptance_88vip_0717_exact135_fitline126_alarm0():
    records = crs.parse_activity_items_export(Path(_ACTIVITY_XLSX).read_bytes())
    assert len(records) == 354                       # 前向填充后的全部"已发布设定" SKU 行

    per_sku = crs.compare_records(records, _load_price_map(), target_tier="big")
    cnt = Counter("贴线" if str(r["verdict"]).startswith("贴线让") else r["verdict"]
                  for r in per_sku)

    # ★回归锚: 135 一分不差 / 126 贴线 / 0 报警 (2026-07-17 人工核对结论)
    assert cnt["一分不差"] == 135, dict(cnt)
    assert cnt["贴线"] == 126, dict(cnt)
    assert cnt.get("超2元报警", 0) == 0, dict(cnt)
    assert cnt.get("偏差", 0) == 0, dict(cnt)

    # 贴线让幅全部 ≤ 0.9 元 (在案审计, spec §四.6e)
    concessions = [r["concession"] for r in per_sku if "concession" in r]
    assert len(concessions) == 126
    assert max(concessions) <= 0.9 + 1e-9

    # 允许存在的非报警类别 (锁样本细节, 防解析回归漂移)
    assert cnt.get("占位", 0) == 90
    assert cnt.get("无映射", 0) == 1
    assert cnt.get("J未刷新", 0) == 2
    assert set(cnt) <= {"一分不差", "贴线", "占位", "无映射", "J未刷新"}


def test_acceptance_discount_export_0717_parses_40_rows():
    records = crs.parse_discount_export(Path(_DISCOUNT_XLSX).read_bytes())
    assert len(records) == 40                        # 41 行含表头 → 40 数据行
    assert {r["activity_name"] for r in records} == {"单品立减0717"}
    assert records[0]["start"] == "2026-07-17 20:00:00"
    assert records[0]["end"] == "2026-07-19 23:59:59"
    assert all(r["sku_id"] and r["discount_value"] is not None for r in records)
