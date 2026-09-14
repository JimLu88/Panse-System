import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_repairs import ordinary_change

@pytest.mark.parametrize('old,new',[('1836.89','1836.89'),('1724.880','1724.88')])
def test_unchanged_deduction_cannot_create_edit_or_replay(old,new):
    assert ordinary_change('1','2','3',dict(current_deduct=old,proposed_deduct=new)) is None

def test_real_change_is_preserved_exactly():
    assert ordinary_change('1','2','3',dict(current_deduct='3244.05',proposed_deduct='3244.74'))==dict(
        item='1',sku='2',offer_id='3',old_deduct='3244.05',new_deduct='3244.74')

@pytest.mark.parametrize('new',['NaN','Infinity','-1','9.99'])
def test_invalid_or_reduced_deduction_is_rejected(new):
    with pytest.raises(ValueError):ordinary_change('1','2','3',dict(current_deduct='10',proposed_deduct=new))
