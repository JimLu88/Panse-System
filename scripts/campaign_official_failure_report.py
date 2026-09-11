"""Read complete official SKU feedback without modifying its XLSX package.

Product-level failure text can name a different SKU than the first physical
row. Only unique full attribute matches inside that product identify a SKU.
This parser returns evidence and limits, never invents ERP prices or consent.
"""
from collections import defaultdict
from decimal import Decimal
import hashlib
import re

from campaign_official_template import read_rows

HEADERS = {'item': '商品ID', 'sku': 'SKUID', 'name': 'SKU名称',
           'submitted_price': '活动价', 'status': '是否成功', 'message': '失败原因或风险提示'}
COUPON = re.compile(r'\[([^\[\]]+?)（活动普惠券后价：([\d.]+)元，最低普惠券后价：([\d.]+)元(?:，[\d.]+折折后券后价：([\d.]+)元)?）\]')
LIST = re.compile(r'您的sku[：:]\s*(.*?)\s+在管控期标价为([\d.]+)元')


def attributes(name):
    return tuple(sorted(x.strip() for x in re.split('[,，;；]', name) if x.strip()))


def amount(value):
    n = Decimal(value)
    if not n.is_finite() or n < 0 or n != n.quantize(Decimal('.01')):
        raise ValueError('invalid_official_amount')
    return format(n, 'f')


def parse_report(raw, *, expected_sha, batch, expected_failed_items):
    result = parse_feedback(raw, expected_sha=expected_sha, batch=batch,
                            expected_items=expected_failed_items)
    if any(row['outcome'] != 'failed' for row in result['outcomes']):
        raise ValueError('official_failed_outcome_not_proven')
    return result


def parse_feedback(raw, *, expected_sha, batch, expected_items, official_counts=None):
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha or not str(batch).isdigit():
        raise ValueError('report_hash_or_batch_invalid')
    expected = list(map(str, expected_items))
    if not expected or len(set(expected)) != len(expected) or not all(i.isdigit() for i in expected):
        raise ValueError('exact_failed_items_required')
    rows = read_rows(raw, '商品SKU导入列表')
    headers = []
    for n, cells in rows.items():
        if all(value in cells.values() for value in HEADERS.values()):
            if any(list(cells.values()).count(value) != 1 for value in HEADERS.values()):
                raise ValueError('duplicate_report_header')
            headers.append((n, {key: next(c for c, v in cells.items() if v == label)
                                for key, label in HEADERS.items()}))
    if len(headers) != 1:
        raise ValueError('official_report_schema_not_unique')
    header_row, columns = headers[0]
    groups, current = {}, None
    seen_pairs = set()
    for n, cells in sorted(rows.items()):
        if n <= header_row:
            continue
        values = {key: cells.get(col, '').strip() for key, col in columns.items()}
        item, sku = values['item'], values['sku']
        # The official explanatory row is not data. Never use a fixed offset.
        if item.startswith('必填') and '必填' in sku:
            continue
        if not any(values.values()):
            continue
        if item:
            if not item.isdigit() or (item in groups and item != current):
                raise ValueError('invalid_or_noncontiguous_report_product')
            current = item
            groups.setdefault(item, {'item': item, 'rows': [], 'messages': [], 'statuses': set()})
        if current is None or not sku.isdigit():
            raise ValueError('orphan_or_invalid_report_sku_row')
        if (current, sku) in seen_pairs:
            raise ValueError('duplicate_report_sku')
        seen_pairs.add((current, sku))
        group = groups[current]
        group['rows'].append(dict(values, item=current, row=n))
        if values['message'] and values['message'] not in group['messages']:
            group['messages'].append(values['message'])
        if values['status']:
            group['statuses'].add(values['status'])
    inferred_success = []
    if set(groups) != set(expected):
        # Some official reports contain failures only. Infer the complement
        # only from the exact bound batch's complete counts, never a preview.
        if (not official_counts or not set(groups).issubset(expected)
                or official_counts.get('pending') != 0 or official_counts.get('total') != len(expected)
                or official_counts.get('failed') != len(groups)
                or official_counts.get('success') != len(expected)-len(groups)
                or any(g['statuses'] != {'失败'} for g in groups.values())):
            raise ValueError('official_report_failed_scope_mismatch')
        inferred_success = sorted(set(expected)-set(groups))
    errors, outcomes = [], []
    for item, group in groups.items():
        if group['statuses'] == {'成功'}:
            group['statuses'] = ['成功']
            outcomes.append({'item': item, 'outcome': 'success'})
            continue
        if group['statuses'] != {'失败'} or not group['messages']:
            raise ValueError('official_failed_outcome_not_proven')
        outcomes.append({'item': item, 'outcome': 'failed'})
        names = defaultdict(list)
        for row in group['rows']:
            names[attributes(row['name'])].append(row)
        for message in group['messages']:
            mentions = []
            for m in COUPON.finditer(message):
                mentions.append((m[1], 'coupon_price', {'observed_final': amount(m[2]),
                    'official_cap': amount(m[4] or m[3])}, m.start(), m.end()))
            for m in LIST.finditer(message):
                mentions.append((m[1], 'list_price', {'official_cap': amount(m[2])}, m.start(), m.end()))
            base = {'item': item, 'batch': str(batch), 'terminal': 'failed', 'message': message,
                    'official_evidence': {'sha256': actual_sha, 'sheet': '商品SKU导入列表',
                                          'product_row': group['rows'][0]['row']}}
            # Detect unparsed price mentions, including malformed/truncated
            # numeric clauses; retaining the raw text alone must not auto-pass.
            remainder = message
            for _, _, _, start, end in sorted(mentions, key=lambda x: x[3], reverse=True):
                remainder = remainder[:start] + remainder[end:]
            incomplete = bool(re.search(r'您的sku[：:]|活动普惠券后价[：:]|您的以下sku', remainder)
                              and (not mentions or re.search(r'您的sku[：:]|活动普惠券后价[：:]', remainder)))
            if not mentions or incomplete:
                errors.append(dict(base, sku='', kind='unknown',
                    parse_issue='unparsed_or_incomplete_official_failure'))
            for name, kind, limits, _, _ in mentions:
                matches = names[attributes(name)]
                if len(matches) != 1:
                    errors.append(dict(base, sku='', kind='unknown', reported_name=name,
                                       parse_issue='reported_sku_name_missing_or_ambiguous'))
                else:
                    row = matches[0]
                    errors.append(dict(base, sku=row['sku'], kind=kind, reported_name=name,
                                       submitted_price=row['submitted_price'], **limits))
        group['statuses'] = sorted(group['statuses'])
    outcomes += [{'item': i, 'outcome': 'success', 'source': 'exact_batch_counts_minus_complete_failure_report'}
                 for i in inferred_success]
    if official_counts and (official_counts.get('pending') != 0
            or official_counts.get('total') != len(outcomes)
            or official_counts.get('success') != sum(r['outcome']=='success' for r in outcomes)
            or official_counts.get('failed') != sum(r['outcome']=='failed' for r in outcomes)):
        raise ValueError('feedback_and_official_batch_counts_disagree')
    return {'batch': str(batch), 'sha256': actual_sha, 'groups': list(groups.values()),
            'errors': errors, 'outcomes': outcomes, 'item_count': len(groups), 'sku_count': len(seen_pairs),
            'complete_file_read': True, 'platform_write': False}
