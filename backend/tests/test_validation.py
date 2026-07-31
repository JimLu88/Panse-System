from app.services import validation


def test_clean_address_not_encrypted():
    r = validation.is_address_encrypted("上海市青浦区徐泾镇绿城春晓园28-702")
    assert r.is_encrypted is False
    assert r.reasons == []


def test_address_with_mask_chars_detected():
    r = validation.is_address_encrypted("浙江省杭州市萧山区**********")
    assert r.is_encrypted is True
    assert any("打码" in reason for reason in r.reasons)
    assert "**********" in r.masked_segments


def test_address_with_keyword_detected():
    r = validation.is_address_encrypted("地址已隐藏")
    assert r.is_encrypted is True
    assert any("隐藏" in reason for reason in r.reasons)


def test_short_address_with_no_mask_clean():
    r = validation.is_address_encrypted("北京市朝阳区xxx")
    assert r.is_encrypted is False  # xxx 不是打码 (只有 2 个连续)


def test_empty_address_not_encrypted():
    assert validation.is_address_encrypted("").is_encrypted is False
    assert validation.is_address_encrypted(None).is_encrypted is False


def test_literal_null_address_component_is_not_usable():
    r = validation.is_address_encrypted("广东省 东莞市 null 大岭山镇")
    assert r.is_encrypted is True
    assert "包含空地址占位符" in r.reasons


def test_chinese_quasi_mask_chars_detected():
    r = validation.is_address_encrypted("江苏省南京市鼓楼区·····路")
    assert r.is_encrypted is True


def test_phone_encryption():
    assert validation.is_phone_encrypted("138****1234") is True
    assert validation.is_phone_encrypted("13812345678") is False
    assert validation.is_phone_encrypted(None) is False
