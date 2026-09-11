"""Read-only calendar adapter. Observed dates are not price coverage windows.

Only the retained Edge named-action bridge may navigate. Calendar cards must
reconcile to the visible total; missing cards are not an empty successful scan.
"""
import re
from datetime import date

ENTRY = 'https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/homepage.htm'
SHOP = '畔色木作'
CARD = re.compile(
    r'(?m)^([^\n]+)\n(?:新\n)?(报名中|可报名|不可报|售卖中|已结束|报名截止|已关闭)\n'
    r'(20\d{2}\.\d{1,2}\.\d{1,2}-(?:20\d{2}\.)?\d{1,2}\.\d{1,2})$')


def checked_snapshot(result, *, title=None):
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise ValueError((result or {}).get('error') or 'official_discovery_not_observed')
    snapshot = result.get('snapshot') or {}
    evidence = result.get('evidence') or {}
    if (result.get('legacy_fallback') is not False or result.get('platform_write') is not False
            or not re.fullmatch('[0-9a-f]{64}', str(evidence.get('sha256', '')))
            or not evidence.get('path') or snapshot.get('expected_shop_verified_on_entry') != SHOP
            or snapshot.get('incomplete_frames') or not snapshot.get('visible_content')):
        raise ValueError('official_discovery_provenance_missing')
    from urllib.parse import urlparse, parse_qs
    url = snapshot.get('page_url', '')
    parsed = urlparse(url)
    if title is None:
        if url != ENTRY:
            raise ValueError('official_discovery_entry_mismatch')
    else:
        query = parse_qs(parsed.query)
        if (parsed.scheme != 'https' or parsed.netloc != 'myseller.taobao.com'
                or parsed.path != '/home.htm/starb/tmc-next/sale/seller/campaign/index.htm'
                or snapshot.get('selected_activity_title') != title
                or snapshot.get('target_shop_verified') is not True
                or any(len(query.get(key, [])) != 1 or not query[key][0].isdigit()
                       for key in ('campaignId', 'unitedActivityId'))):
            raise ValueError('official_discovery_detail_mismatch')
    texts = []
    for block in snapshot['visible_content']:
        if block.get('truncated') or not isinstance(block.get('text'), str):
            raise ValueError('official_discovery_truncated')
        texts.append(block['text'])
    return snapshot, '\n'.join(texts), evidence


def calendar(result):
    snapshot, text, evidence = checked_snapshot(result)
    counts = re.findall(r'共\s*(\d+)\s*场', text)
    if len(counts) != 1:
        raise ValueError('official_calendar_total_not_unique')
    cards = []
    for match in CARD.finditer(text):
        title, status, interval = match.groups()
        first, last = interval.split('-')
        start = date(*map(int, first.split('.')))
        end_parts = list(map(int, last.split('.')))
        # Only the explicit leading year of this very same printed interval
        # may fill the shortened end. Never infer a year from today's date.
        end = date(*end_parts) if len(end_parts) == 3 else date(start.year, *end_parts)
        if end < start:
            raise ValueError('official_calendar_ambiguous_year_boundary')
        cards.append({'title': title, 'status': status, 'start': start.isoformat(),
                      'end': end.isoformat(), 'raw': match.group(),
                      'date_precision': 'day_only', 'page_evidence': evidence,
                      'exact_activity_identity_verified': False})
    if len(cards) != int(counts[0]) or len({c['title'] for c in cards}) != len(cards):
        raise ValueError('official_calendar_coverage_mismatch')
    return {'ok': True, 'campaigns': cards, 'count': len(cards), 'calendar_opened': True,
            'page_evidence': evidence, 'observed_links': snapshot.get('observed_links', []),
            'legacy_fallback': False, 'platform_write': False,
            'exact_activity_identity_verified': False}


def detail(result, title):
    snapshot, text, evidence = checked_snapshot(result, title=title)
    return {'ok': True, 'campaign_title': title, 'url': snapshot['page_url'],
            'body_text': text, 'actual_titles': [title], 'page_evidence': evidence,
            'legacy_fallback': False, 'platform_write': False,
            'exact_activity_identity_verified': False}


def observe(db, *, title=None):
    from app.services import web_agent_service
    # On-demand start is bounded; the scheduler's persisted 72-hour timestamp
    # prevents repeated wake attempts when the user must restore login/service.
    online = web_agent_service.ensure_online(db, reason='campaign_official_discovery', wait_s=60)
    if not online.get('online'):
        return {'ok': False, 'error': online.get('error') or 'dedicated_edge_offline',
                'legacy_fallback': False, 'platform_write': False}
    body = {'stage': 'detail' if title is not None else 'home', 'expected_shop': SHOP}
    if title is not None:
        if not isinstance(title, str) or not re.fullmatch(r'(?:20\d{2}年|\d{2}年)淘宝[^\n]{1,60}', title):
            return {'ok': False, 'error': 'exact_visible_campaign_title_required',
                    'legacy_fallback': False, 'platform_write': False}
        body['title'] = title
    result = web_agent_service._post_raw(db, '/api/campaign/continuous/observe', body, timeout=30)
    try:
        return detail(result, title) if title is not None else calendar(result)
    except (ValueError, TypeError, KeyError) as exc:
        return {'ok': False, 'error': str(exc), 'legacy_fallback': False, 'platform_write': False}
