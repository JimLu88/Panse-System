"""One current user authorization, not a global tolerance or price adjustment."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

RECEIPT = Path(__file__).resolve().parents[1] / 'docs/receipts/campaign-autumn-two-yuan-authorization-20260911.json'
RECEIPT_SHA256 = 'b1a641b9e147f17b3c073408779ad477ef106676f0adbbd1ddcd8e23d6f74522'
SCOPE = ('49557/49560/3538210379', '2026-09-16 20:00:00', '2026-09-27 23:59:59', Decimal('0.12'), 'big')


def policy_for(campaign, start, end, rate, target):
    if (campaign, start, end, Decimal(str(rate)), target) != SCOPE:
        return None
    raw = RECEIPT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != RECEIPT_SHA256:
        raise ValueError('scoped_tolerance_authorization_changed')
    doc = json.loads(raw)
    return dict(authorization=RECEIPT.name, authorization_sha256=RECEIPT_SHA256,
                campaign=doc['campaign'], start=doc['start'], end=doc['end'],
                official_rate=doc['official_rate'], target=doc['target'],
                max_absolute_delta_cny=doc['max_absolute_delta_cny'], inclusive=True)


def validate_bundle_policy(body):
    from campaign_official_template import discount_rate
    policy = policy_for(body['campaign'], body['start'], body['end'],
                        discount_rate(body['official_rate']), body['target'])
    if body.get('final_price_tolerance') != policy:
        raise ValueError('scoped_tolerance_version_changed_regenerate_local_files')
    return policy
