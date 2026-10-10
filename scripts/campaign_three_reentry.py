"""Exact re-entry after the user's verified three-item Double11 withdrawal.

Original attempts remain unchanged. The retired success is waived only inside
this explicitly selected authority instance; any later attempt or unknown is
still protected. This does not affect ordinary/global campaign execution.
"""
import hashlib
import json
from pathlib import Path
from campaign_entry_authority import Authority

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010')
CAMPAIGN='49646/49651/3555037899'
ITEMS={'1090473184978','1089439705938','1089444961055'}
OLD_CLAIM='a278f5a2fb64423f891e6e688d098a91'
WITHDRAWAL_SHA='5fb4b139e97ece06069f4ec2a7686259bde78d33e7b2c901ea1c19a5ef2c2bd4'


def eligible(old_attempts,item):
    protected=[r for r in old_attempts if r['item']==item and r['status'] in ('success','unknown')]
    return item in ITEMS and len(protected)==1 and protected[0]['status']=='success' and protected[0]['id']==OLD_CLAIM+':'+item


def verify_withdrawal():
    raw=(ROOT/'withdrawal/terminal.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=WITHDRAWAL_SHA:raise ValueError('withdrawal_receipt_changed')
    d=json.loads(raw)
    if d['state']!='withdrawn_verified' or {r['item'] for r in d['items'] if r['state']=='withdrawn_verified'}!=ITEMS:
        raise ValueError('exact_three_withdrawal_not_verified')
    publication=json.loads((ROOT/'rotation/correction/replacement-publication/terminal.json').read_text(encoding='utf-8'))
    if {r['item'] for r in publication.get('items',[]) if r['state']=='published_verified'}!=ITEMS:
        raise ValueError('replacement_publication_not_complete')


class ThreeReentryAuthority(Authority):
    def blocked(self,campaign,phase,start,end,*,historical_only=False):
        result=super().blocked(campaign,phase,start,end,historical_only=historical_only)
        if phase!='signup' or campaign!=CAMPAIGN or (start,end)!=('2026-10-20 20:00:00','2026-11-13 23:59:59'):return result
        verify_withdrawal()
        attempts=[dict(r) for r in self.db.execute('SELECT id,item,status FROM attempts WHERE campaign=? AND phase=?',(campaign,phase))]
        # A separately registered outcome is never erased by this exact attempt
        # waiver. Current source inspection found no such outcomes for these 3.
        extra=set()
        for source in self.sources():
            doc=source['document'] or {}
            if doc.get('phase')=='signup' and doc.get('campaign')==campaign:
                extra.update(r['item'] for r in doc.get('items',[]) if r.get('status') in ('success','unknown'))
            if '/'.join(str(doc.get(k,'')) for k in ('campaign_id','united_activity_id','sign_record_id'))==campaign:
                extra.update(doc.get('protected_successful_items',[]))
        for item in ITEMS-extra:
            if result.get(item)=='success' and eligible(attempts,item):result.pop(item)
        return result
