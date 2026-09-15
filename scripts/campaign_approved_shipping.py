"""One user-approved shipping commitment for one failed super-reduce batch."""
import hashlib
from pathlib import Path

ITEM='1001358847694'
CAMPAIGN='legacy/itemApply/3172207691'
BATCH='861801345'
SKUS={'5991470332907','5991470332908','6001467129246','6231821270898','6001467129247'}
SOURCE=Path(__file__).resolve().parents[1]/'docs/receipts/campaign-three-decisions-20260915.json'
SHA='0fe90c09190ce43e2796df40d155e9dbd9c29d5ca546bece153d5aedc60407d1'


def repair_for(item,sku,batch):
    if str(item)!=ITEM or str(sku) not in SKUS or str(batch)!=BATCH:return None
    if not SOURCE.is_file() or hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SHA:return None
    return {'kind':'file_shipping','value':'1','authorization_sha256':SHA,'failed_batch':BATCH}


def decision(error):
    if error.get('parse_issue')!='free_shipping_commitment_required':return None
    return repair_for(error.get('item'),error.get('sku'),error.get('batch'))


def validate(item,sku,repair,identity):
    key='/'.join(str(identity.get(k,'')) for k in ('campaign_id','phase_id','sign_record_id'))
    if key!=CAMPAIGN or repair!=repair_for(item,sku,repair.get('failed_batch')) or repair is None:
        raise ValueError('shipping_outside_exact_user_authorization')


def projection_values(corrections,identity):
    shipping={}
    for item,ds in corrections.items():
        for d in ds:
            if d['repair']['kind']!='file_shipping':continue
            validate(item,d['sku'],d['repair'],identity)
            shipping[item]='1'
    return shipping
