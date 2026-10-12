import pytest
from campaign_correction_scope import correction_row

def test_current_failed_row_keeps_original_correction():
    row={'activity_price':'1466.66'}
    assert correction_row(('1','2'),{('1','2'):row},{}) is row

def test_confirmed_success_is_omitted_not_reenrolled():
    assert correction_row(('1','2'),{},{'1':'success'}) is None

@pytest.mark.parametrize('status',[None,'unknown','failed','pending'])
def test_other_missing_scope_is_not_silently_dropped(status):
    with pytest.raises(ValueError,match='outside_current_unsuccessful_scope'):
        correction_row(('1','2'),{},{'1':status})
