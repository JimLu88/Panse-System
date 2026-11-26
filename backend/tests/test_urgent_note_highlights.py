from copy import deepcopy
from datetime import date

import pytest

from app.services.urgent_note_highlights import note_signal
from app.services import urgent_fulfillment_feishu as u


@pytest.mark.parametrize("note,level", [
    ("非常紧急，请尽快发", "red"), ("取消加急", ""), ("不着急", ""),
    ("不用加急；12月3号前到货", "yellow"), ("能否加急？", "yellow"),
    ("2026-11-28前发货", "red"), ("2026-12-20前到货", "yellow"),
    ("12月3号前到货", "yellow"), ("下单后20天内做好", "yellow"),
    ("今天发货", "yellow"), ("原木色，深度360", ""),
    ("无需加急，但是非常紧急的那件先发", "red"),
    ("2026-02-30发货", ""), ("不再按2026-11-27发货", ""),
    ("2.2*0.9定制", ""), ("0.95米", ""), ("退差价6800-5626=1174元", ""),
    ("2026-11-25 用户核定配件", ""), ("2026-11-16 用户确认：开始制作", ""),
    ("平台要求2026年12月25日20:52前发货", "yellow"),
])
def test_note_signals(note, level):
    assert note_signal([note], date(2026, 11, 26))[0] == level


@pytest.mark.parametrize("value,color", [
    ("2026-11-20", u.COLOR_RED), ("2026-12-01", u.COLOR_RED),
    ("2026-12-02", u.COLOR_YELLOW), ("2026-12-07", u.COLOR_YELLOW),
    ("2026-12-08", u.COLOR_NEUTRAL), ("待核实", u.COLOR_YELLOW),
])
def test_date_color_boundaries(value, color):
    assert u._tag_color("目标日期", value, date(2026, 11, 26)) == color


def test_schema_conversion_retains_values_and_option_ids(monkeypatch):
    fields = {n: {"field_name": n, "field_id": n, "type": 1, "property": None} for n in u.TAG_FIELDS}
    fields["备注提醒"].update(type=3, property={"options": [{"id": "existing", "name": "有时间要求 / 请核实", "color": 0}]})
    current = {"key": {"fields": {"记录键": "key", "紧急程度": "逾期", "目标日期": "2026-11-20"}}}
    writes = []
    monkeypatch.setattr(u.fc, "list_table_fields", lambda *a: deepcopy(list(fields.values())))
    monkeypatch.setattr(u.fc, "list_records", lambda *a: deepcopy(list(current.values())))
    def update(*a, **kw):
        fields[kw["field_name"]].update(type=kw["field_type"], property=deepcopy(kw["property_"]))
        writes.append(kw)
    monkeypatch.setattr(u.fc, "update_field", update)
    u._ensure_colors(None, "base", "owned", current, {}, "2026-11-26")
    assert len(writes) == 3
    assert fields["备注提醒"]["property"]["options"][0]["id"] == "existing"
    assert fields["目标日期"]["property"]["options"][0]["color"] == u.COLOR_RED
    u._ensure_colors(None, "base", "owned", current, {}, "2026-11-26")
    assert len(writes) == 3


def test_note_color_does_not_replace_confirmed_date():
    row = {"order_no": "o", "sub_order_nos": ["active"], "label": "单", "days": 20,
           "due": "2026-12-16", "action": "发货", "reason": "备注要求"}
    source = [{"fields": {"订单号": "o", "子订单号": "active", "订单备注": "非常紧急"}},
              {"fields": {"订单号": "o", "子订单号": "refunded", "订单备注": "2026-11-01发货"}}]
    fields = u.build_rows([row], source, "2026-11-26", "now")["2026-11-26:o"]
    assert fields["目标日期"] == "2026-12-16"
    assert fields["紧急程度"] == "非常紧急"
    assert "2026-11-01" not in fields["备注时间要求"]
