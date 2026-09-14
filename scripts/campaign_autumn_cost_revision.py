"""Explicit Sep14 user grant: one autumn offer, five ordinary rock-table SKUs.

Not a standing tolerance/rotation rule. Inputs cannot supply prices or scope.
Reuse the existing consume-once verifier and recorded discount_reprice driver.
"""
from decimal import Decimal
from pathlib import Path
import sys

import campaign_cost_revision as core
from campaign_entry_authority import load, file_sha
from campaign_continuous_policy import fingerprint, RULE_SHA

GRANT='rocktable-autumn-current-cost-20260914-v1'
ITEM='792992319206'
OFFER='144956016253'
CAMPAIGN='49557/49560/3538210379'
WINDOW={'start':'2026-09-16 20:00:00','end':'2026-09-27 23:59:59'}
SKUS=tuple(str(s) for s in range(6126676768459,6126676768464))
EXCLUDED=set()  # Read this grant's five ordinary rows only; never custom rows.
RATE,TARGET=Decimal('.12'),'big'
VERSION='a6517cf55860c21f14f2e792e97f7a24c5e6a280d9d329cf49a4cd75eaa274a0'
PARENT='1bd5fc2953379e383bc1203c6d36c26541922eccee9561c06c68cf7d0efa97f8'
SEGMENT='485511952e858a1ccb16e04a7541eb6368ad2c5cb819b1701d4b1f897a7aac1f'
ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-continuous')
SNAPSHOT=ROOT/'runs'/PARENT/'segments'/SEGMENT/'resolved-snapshot.json'
SNAPSHOT_SHA='9c968a249e80db523711f8a245b14dad9c331b364e78e21eaa088cc184fed299'
REQUEST={'schema':'continuous_campaign_autumn_cost_v1','rule_sha':RULE_SHA,
         'parent_request_id':PARENT,'grant_id':GRANT,'items':[ITEM],'skus':list(SKUS)}
RID=fingerprint(REQUEST)
READ_ID=fingerprint([GRANT,'original-five-discounts'])
RUN=ROOT/'runs'/RID


def validate_request(request):
    if request!=REQUEST:raise ValueError('exact_user_autumn_five_sku_grant_required')
    return '畔色木作'


def calculate(snapshot,read):
    return core.calculate(snapshot,read,policy=sys.modules[__name__])


def check_current(a,body):
    return core.check_current(a,body,policy=sys.modules[__name__])


def validate_proof(body,cid,jid,proof):
    return core.validate_proof(body,cid,jid,proof,policy=sys.modules[__name__])


def derive():
    if file_sha(SNAPSHOT)!=SNAPSHOT_SHA:raise ValueError('autumn_snapshot_changed')
    path=RUN/'price'/'discount_readback-observation.json'
    job=load(path);read=job['result']
    if (job.get('operation')!='discount_readback' or job.get('state')!='finished'
            or job.get('job_id')!=fingerprint(['discount_readback','畔色木作',READ_ID])
            or read.get('read_request_id')!=READ_ID):
        raise ValueError('autumn_recorded_original_discount_read_required')
    evidence=Path(read['evidence_path']).resolve(strict=True)
    allowed=Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')/job['job_id']
    if not evidence.is_relative_to(allowed.resolve()):raise ValueError('autumn_read_evidence_outside_job')
    saved=load(evidence)
    if any(saved.get(k)!=read.get(k) for k in ('state','rows','shop_name','read_request_id','price_window','platform_write')):
        raise ValueError('autumn_read_observation_changed')
    recording=read['recording'];video=Path(recording['video']).resolve(strict=True)
    if (not video.is_relative_to(allowed.resolve()) or recording.get('frames',0)<1
            or file_sha(video)!=recording['video_sha256']):
        raise ValueError('autumn_read_recording_changed')
    rows=calculate(load(SNAPSHOT),read)
    return dict(grant_id=GRANT,campaign=CAMPAIGN,shop_name='畔色木作',**WINDOW,
        price_version=VERSION,rows=rows,
        sources=[{'path':str(p),'sha256':file_sha(p)} for p in (SNAPSHOT,path,evidence)],
        automatic_retry=False,rotation_performed=False,
        user_approval='call_qo7OzCr0whHiMaVdacqX2IDY: 允许，仅这件商品这 5 条 SKU')


def execute(request,*,root,authority,edge,artifact_roots):
    from campaign_autumn_cost_completion import execute as complete
    return complete(request,root=root,authority=authority,edge=edge,artifact_roots=artifact_roots)
