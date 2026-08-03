from app.services import taobao_order_import as toi


def test_masked_private_value_is_not_inserted():
    assert toi._prefer_clear_private_value(None, "浙江省杭州市********") is None
    assert toi._prefer_clear_private_value(None, "138****1234", phone=True) is None


def test_clear_value_replaces_masked_value():
    assert (
        toi._prefer_clear_private_value(
            "浙江省杭州市********", "浙江省杭州市西湖区文一路1号"
        )
        == "浙江省杭州市西湖区文一路1号"
    )
    assert (
        toi._prefer_clear_private_value("138****1234", "13812345678", phone=True)
        == "13812345678"
    )


def test_masked_reimport_never_downgrades_clear_value():
    clear = "浙江省杭州市西湖区文一路1号"
    assert toi._prefer_clear_private_value(clear, "浙江省杭州市********") == clear


def test_complete_address_drops_taobao_null_district_token():
    incoming = "广东省 东莞市 null 虎门镇龙眼 十五路60号A栋一楼3号门"
    assert toi._prefer_clear_private_value(None, incoming) == (
        "广东省 东莞市 虎门镇龙眼 十五路60号A栋一楼3号门"
    )


def test_sparse_null_address_remains_protected():
    assert toi._prefer_clear_private_value(None, "广东省 东莞市 null 大岭山镇") is None
