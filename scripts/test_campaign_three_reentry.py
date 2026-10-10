from campaign_three_reentry import eligible,OLD_CLAIM

ITEM='1090473184978'

def original():return {'id':OLD_CLAIM+':'+ITEM,'item':ITEM,'status':'success'}

def test_only_exact_old_success_can_reenter():assert eligible([original()],ITEM)

def test_unknown_never_reenters():
    r=original();r['status']='unknown';assert not eligible([r],ITEM)

def test_new_success_stays_protected():
    assert not eligible([original(),dict(id='new:'+ITEM,item=ITEM,status='success')],ITEM)

def test_new_unknown_stays_protected():
    assert not eligible([original(),dict(id='new:'+ITEM,item=ITEM,status='unknown')],ITEM)

def test_other_products_never_reenter():assert not eligible([original()],'123')

def test_other_claim_never_reenters():
    r=original();r['id']='other:'+ITEM;assert not eligible([r],ITEM)
