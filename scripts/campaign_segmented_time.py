"""Pure, versioned time allocation; no browser reads or price/offer writes.

Official long-term enrollment validity and a discount's pricing period are
different facts. Inputs come from the already required activity discovery.
Coverage is explicit: no guessed future campaigns or default 2028 discount.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from campaign_continuous_policy import RULE_SHA as CONTINUOUS_SHA, fingerprint

CONTRACT = Path(__file__).resolve().parents[1] / 'docs/campaign-segmented-time-20260911.json'
RULE_SHA = '65f805171600b71cc3e7d23dcab54442cfda25740330a66fc8508ab3acf3445d'
STAMP = '%Y-%m-%d %H:%M:%S'
SECOND = timedelta(seconds=1)


def load_rules():
    rules = json.loads(CONTRACT.read_text(encoding='utf-8'))
    if fingerprint(rules) != RULE_SHA:
        raise ValueError('segmented_time_rules_changed_require_user_instruction')
    return rules


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
        raise ValueError('time_requires_exact_shanghai_seconds')
    return datetime.strptime(value, STAMP)


def window(value):
    start, end = instant(value['start']), instant(value['end'])
    if start > end:
        raise ValueError('inverted_time_window')
    return start, end


def _activity(value, shop, kind):
    if value.get('shop_id') != shop:
        raise ValueError('time_plan_shop_mismatch')
    if (not re.fullmatch(r'\d+/\d+/\d+', str(value.get('campaign', '')))
            or not value.get('page_evidence')):
        raise ValueError('time_plan_official_identity_or_evidence_missing')
    start, end = window(value)
    rate = Decimal(str(value.get('official_rate')))
    if not rate.is_finite() or not 0 <= rate < 1:
        raise ValueError('time_plan_official_rate_invalid')
    if kind == 'campaign' and rate not in (Decimal('.12'), Decimal('.15')):
        raise ValueError('time_plan_campaign_rate_outside_current_rules')
    return dict(campaign=value['campaign'], shop_id=shop, kind=kind,
                target='medium' if kind == 'daily' else 'big', official_rate=str(rate),
                official_window={'start':start.strftime(STAMP), 'end':end.strftime(STAMP)},
                page_evidence=value['page_evidence'])


def build_plan(request):
    load_rules()
    if (request.get('rule_sha') != RULE_SHA or request.get('timezone') != 'Asia/Shanghai'
            or request.get('known_campaigns_complete') is not True
            or not request.get('discovery_evidence')
            or not request.get('shop_id') or not request.get('price_version')):
        raise ValueError('time_plan_inputs_incomplete')
    lo, hi = window(request['coverage'])
    daily = _activity(request['daily_activity'], request['shop_id'], 'daily')
    first, last = window(daily['official_window'])
    if lo < first or hi > last:
        raise ValueError('coverage_outside_daily_platform_validity')
    campaigns = {}
    for source in request['campaigns']:
        entry = _activity(source, request['shop_id'], 'campaign')
        if entry['campaign'] == daily['campaign']:
            raise ValueError('same_identity_cannot_be_daily_and_campaign')
        prior = campaigns.get(entry['campaign'])
        if prior and prior != entry:
            raise ValueError('conflicting_official_campaign_identity')
        campaigns[entry['campaign']] = entry
    selected = []
    for entry in campaigns.values():
        start, end = window(entry['official_window'])
        if end < lo or start > hi:
            continue
        # Big campaign pricing must use the exact sale window, not a clipped
        # segment chosen just because a rolling scheduler horizon ends there.
        if start < lo or end > hi:
            raise ValueError('coverage_cuts_campaign_window_extend_explicit_horizon')
        selected.append((start, end, entry))
    selected.sort(key=lambda x: (x[0], x[1], x[2]['campaign']))
    segments = []

    def add(entry, start, end):
        segment = dict(entry, price_version=request['price_version'], rule_sha=RULE_SHA,
                       price_window={'start':start.strftime(STAMP), 'end':end.strftime(STAMP)})
        segment['segment_id'] = fingerprint(segment)
        segments.append(segment)

    cursor = lo
    for start, end, entry in selected:
        if start < cursor:
            raise ValueError('overlapping_campaigns_need_explicit_resolution')
        if cursor < start:
            add(daily, cursor, start-SECOND)
        add(entry, start, end)
        cursor = end+SECOND
    if cursor <= hi:
        add(daily, cursor, hi)
    plan = dict(schema='campaign_segmented_time_v1', rule_sha=RULE_SHA,
                request_sha256=fingerprint(request), timezone='Asia/Shanghai',
                coverage=request['coverage'], segments=segments, platform_write=False)
    plan['plan_id'] = fingerprint(plan)
    return plan


def bind_request(path, segment_id):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    plan = build_plan(json.loads(raw))
    matches = [s for s in plan['segments'] if s['segment_id'] == segment_id]
    if len(matches) != 1:
        raise ValueError('time_segment_missing_or_changed')
    return dict(rule_sha=RULE_SHA, request_path=str(path),
                request_file_sha256=hashlib.sha256(raw).hexdigest(),
                plan_id=plan['plan_id'], segment=matches[0])


def validate_binding(body):
    """Replay the pure calculation at the existing generation/claim boundary.

    Old bundles without this opt-in stay byte/semantic compatible. No network
    preflight is introduced. Mutating source/period/target invalidates the file.
    """
    binding = body.get('time_binding')
    if binding is None:
        return None
    if body.get('continuous_rule_sha') != CONTINUOUS_SHA:
        raise ValueError('segmented_time_requires_approved_continuous_rule')
    actual = bind_request(binding['request_path'], binding['segment']['segment_id'])
    if binding != actual:
        raise ValueError('time_plan_source_or_binding_changed')
    segment = actual['segment']
    from campaign_official_template import discount_rate
    if (body['campaign'] != segment['campaign'] or body['target'] != segment['target']
            or body['price_version'] != segment['price_version']
            or discount_rate(body['official_rate']) != Decimal(segment['official_rate'])
            or {'start':body['start'], 'end':body['end']} != segment['price_window']):
        raise ValueError('time_segment_campaign_price_or_window_mismatch')
    return segment


def phase_window(body, phase):
    if phase not in ('signup', 'discount'):
        raise ValueError('invalid_phase')
    segment = validate_binding(body)
    if segment and phase == 'signup':
        return dict(segment['official_window'])
    return {'start':body['start'], 'end':body['end']}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request', type=Path)
    args = parser.parse_args()
    print(json.dumps(build_plan(json.loads(args.request.read_text(encoding='utf-8'))),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
