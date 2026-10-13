from copy import deepcopy
from decimal import Decimal
import pytest
import campaign_cabinet_oct13_repair as c


def test_pinned_sources_derive_eight_bounded_changes():
    doc=c.source();specs,expected,wanted=c.derive(doc)
    old=doc['original_offer_readback']['values']
    changed={s for s in wanted if Decimal(wanted[s])!=Decimal(old[s])}
    assert len(changed)==8 and len(specs)==16 and len(expected)==18
    assert {Decimal(wanted[s])-Decimal(old[s]) for s in changed}=={Decimal('.25'),Decimal('.5'),Decimal('.75')}
    assert wanted['6144517572907']=='80.82' and specs['6144517572907']['daily']=='300.00'
    assert expected['6144517572907'] is None


@pytest.mark.parametrize('field,value', [('活动普惠券后价','1'),('最低普惠券后价要求','1'),('活动价','1'),('营销ID','wrong')])
def test_no_price_or_scope_override(field,value):
    doc=deepcopy(c.source());doc['rows'][0]['official'][field]=value
    with pytest.raises(ValueError):c.derive(doc)


def test_saved_discount_drift_and_no_duplicate_amend():
    read=deepcopy(c.source()['original_offer_readback'])
    read['not_enrolled']=[s for s in c.SKUS if s not in read['values']]
    wanted=c.deductions()
    assert len(c.adjustment(read,wanted))==8
    read['values'].update(wanted)
    assert c.adjustment(read,wanted)=={}
    read['values']['6292847403152']='1'
    with pytest.raises(ValueError,match='drift'):c.adjustment(read,wanted)


def test_editor_preserves_custom_and_nonblank_daily():
    rows=[dict(sku_id=s,inputs=[dict(value=v or '')]) for s,v in c.INITIAL.items()]
    assert c.editor_prices({'rows':rows})==c.INITIAL
    rows[-1]['inputs'][0]['value']='1'
    with pytest.raises(ValueError,match='drift'):c.editor_prices({'rows':rows})
