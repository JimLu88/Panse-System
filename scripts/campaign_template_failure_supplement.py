"""Resolve only a named, truncated listed-price clause from a bound official template.

The original report is immutable. This is complementary evidence, never a claim
that the missing text was downloaded, and never a replacement for other errors.
"""
from copy import deepcopy
import hashlib
import re

from campaign_official_failure_report import attributes, amount
from campaign_official_template import read_rows


def supplement(errors, raw, *, expected_sha, evidence):
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('supplement_template_changed')
    rows = read_rows(raw, '商品SKU导入列表')
    headers = [(n, cells) for n, cells in rows.items()
               if all(v in cells.values() for v in ('商品ID','SKUID','SKU名称','最低标价'))]
    if len(headers) != 1:
        raise ValueError('supplement_template_header_not_unique')
    n, header = headers[0]
    labels = {'item':'商品ID','sku':'SKUID','name':'SKU名称','cap':'最低标价'}
    if any(list(header.values()).count(v) != 1 for v in labels.values()):
        raise ValueError('supplement_template_duplicate_header')
    cols = {k:next(c for c,v in header.items() if v == label) for k,label in labels.items()}
    current = None; facts = []
    for row, cells in sorted(rows.items()):
        if row <= n: continue
        item = cells.get(cols['item'],'').strip()
        sku = cells.get(cols['sku'],'').strip()
        if item.isdigit(): current = item
        elif item: current = None
        if current and sku.isdigit():
            facts.append(dict(item=current,sku=sku,name=cells.get(cols['name'],''),
                              cap=cells.get(cols['cap'],''),row=row,column=cols['cap']))
    result = []; count = 0
    for error in errors:
        if error.get('kind') != 'unknown' or error.get('parse_issue') != 'unparsed_or_incomplete_official_failure':
            result.append(deepcopy(error)); continue
        # Only this exact truncated grammar is understood. A partial name,
        # another error, missing number after 为, or unknown suffix stays held.
        tail = re.search(r'您的sku[：:]\s*([^\r\n]+?)\s+在管\s*$',error['message'])
        if not tail:
            result.append(deepcopy(error)); continue
        matches = [r for r in facts if r['item']==error['item'] and attributes(r['name'])==attributes(tail[1])]
        if len(matches)!=1 or not matches[0]['cap']:
            raise ValueError('supplement_exact_sku_cap_not_unique')
        row = matches[0]
        submitted = evidence['submitted_prices'].get((row['item'],row['sku']))
        if submitted is None: raise ValueError('supplement_sku_not_in_failed_submission')
        entry = {k:deepcopy(v) for k,v in error.items() if k not in ('parse_issue','constraints')}
        entry.update(kind='list_price',sku=row['sku'],reported_name=tail[1],
                     submitted_price=submitted,official_cap=amount(row['cap']),
                     supplemental_evidence={'path':evidence['path'],'sha256':expected_sha,
                         'sheet':'商品SKU导入列表','cell':row['column']+str(row['row']),
                         'source':'same_campaign_official_template_reference',
                         'original_message_preserved':True})
        result.append(entry); count += 1
    if count == 0: raise ValueError('no_proven_truncated_list_clause')
    return result
