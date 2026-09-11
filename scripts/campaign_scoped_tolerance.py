"""One current user authorization, not a global tolerance or price adjustment."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

RECEIPT = Path(__file__).resolve().parents[1] / 'docs/receipts/campaign-autumn-two-yuan-authorization-20260911.json'
RECEIPT_SHA256 = 'b1a641b9e147f17b3c073408779ad477ef106676f0adbbd1ddcd8e23d6f74522'
SCOPE = ('49557/49560/3538210379', '2026-09-16 20:00:00', '2026-09-27 23:59:59', Decimal('0.12'), 'big')


def policy_for(campaign, start, end, rate, target, *, continuous_rule_sha=None):
    if continuous_rule_sha is not None:
        from campaign_continuous_policy import RULE_SHA, load_rules
        from datetime import datetime
        import re
        rules = load_rules()
        if continuous_rule_sha != RULE_SHA:
            raise ValueError('unapproved_continuous_rule_version')
        if (not re.fullmatch(r'\d+/\d+/\d+', str(campaign))
                or datetime.fromisoformat(start) > datetime.fromisoformat(end)):
            raise ValueError('continuous_tolerance_exact_identity_required')
        numeric_rate = Decimal(str(rate))
        if not numeric_rate.is_finite() or not 0 <= numeric_rate < 1:
            raise ValueError('invalid_official_rate')
        if target not in ('medium', 'big') or (numeric_rate in (Decimal('.12'), Decimal('.15')) and target != 'big'):
            raise ValueError('continuous_target_mismatch')
        return dict(authorization=rules['rule_id'], authorization_sha256=RULE_SHA,
                    campaign=campaign, start=start, end=end, official_rate=str(numeric_rate),
                    target=target, max_absolute_delta_cny=rules['maximum_final_delta_cny'], inclusive=True)
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
                        discount_rate(body['official_rate']), body['target'],
                        continuous_rule_sha=body.get('continuous_rule_sha'))
    if body.get('final_price_tolerance') != policy:
        raise ValueError('scoped_tolerance_version_changed_regenerate_local_files')
    return policy
