"""Actual registered discount amounts, not unuploaded ideal deductions.

Saved evidence only; no remote refresh or price change. A byte-pinned current
user exception may allow a scoped final delta; no automatic tolerance grant.
Historical receipts prove that import, not a fresh current platform readback.
"""
from decimal import Decimal
from pathlib import Path


def historical_offer(document):
    from campaign_entry_authority import file_sha, load
    from campaign_official_template import read_rows
    proof_path, receipt_path = Path(document['execution_path']), Path(document['rows_path'])
    if file_sha(proof_path) != document['execution_sha256'] or file_sha(receipt_path) != document['rows_sha256']:
        raise ValueError('discount_evidence_source_changed')
    proof, receipt = load(proof_path)[document['section']], load(receipt_path)
    if proof.get('status') != 'official_import_success' or not proof.get('official_receipt'):
        raise ValueError('discount_official_success_missing')
    if (proof['start'], proof['end']) != (receipt['window']['start'], receipt['window']['end']):
        raise ValueError('discount_receipt_window_mismatch')
    files = [f for f in receipt['files'] if f['sha256'] == proof['sha256']]
    if len(files) != 1 or file_sha(files[0]['path']) != proof['sha256']:
        raise ValueError('discount_uploaded_file_not_bound_to_success')
    values = read_rows(Path(files[0]['path']).read_bytes(), 'Sheet1')
    rows = [dict(item=r['A'], sku=r['B'], deduct=r['C']) for n,r in values.items() if n > 1 and r.get('A')]
    if len(rows) != proof['skus'] or len({(r['item'],r['sku']) for r in rows}) != len(rows) or len({r['item'] for r in rows}) != proof['items']:
        raise ValueError('discount_success_scope_mismatch')
    return dict(offer_id=proof['activity_id'], start=proof['start'], end=proof['end'], rows=rows,
                items=[dict(item=i,status='success') for i in sorted({r['item'] for r in rows})],
                evidence=str(proof_path), evidence_sha256=document['execution_sha256'],
                evidence_kind='historical_official_import_receipt_not_fresh_readback')


def verify_partial_added_scope(offer,original):
    """Only independently saved inclusions may extend an original partial import."""
    from campaign_discount_include import pinned
    old={(r['item'],r['sku']) for r in original}
    current={(r['item'],r['sku']) for r in offer['rows']}
    if len(current)!=len(offer['rows']) or not old.issubset(current):raise ValueError('partial_discount_amendment_scope_changed')
    extra=current-old
    if not extra:return
    verified=set()
    for inclusion in offer.get('verified_include_evidence',[]):
        ref=inclusion['receipt'];job=pinned(ref);raw=pinned(ref['original'])
        result=job.get('result') or {}
        if (job.get('operation')!='discount_include' or job.get('state')!='finished'
                or raw.get('state')!='verified_saved' or any(result.get(k)!=raw.get(k) for k in ('claim_id','rows','state'))):raise ValueError('partial_include_receipt_changed')
        values={}
        for row in raw['rows']:
            if (row.get('state')!='verified_saved' or row['window'].get('offer_id')!=offer.get('platform_offer_id',offer['offer_id'])
                    or any(row['window'].get(k)!=offer[k] for k in ('start','end'))):raise ValueError('partial_include_window_changed')
            for sku,amount in row['values'].items():values[row['item'],sku]=Decimal(str(amount))
        wanted={(r['item'],r['sku']):Decimal(str(r['deduct'])) for r in inclusion['rows']}
        if values!=wanted:raise ValueError('partial_include_amount_changed')
        for pair,amount in values.items():
            matches=[r for r in offer['rows'] if (r['item'],r['sku'])==pair]
            if len(matches)!=1 or Decimal(str(matches[0]['deduct']))!=amount:raise ValueError('partial_include_projection_changed')
        verified.update(values)
    if not extra.issubset(verified):raise ValueError('partial_discount_amendment_scope_changed')


def reconcile(activity, planned, offers, start, end, rate, *, excluding_offer=None, campaign=None, target=None, continuous_rule_sha=None):
    """Partition new/reused rows; report unknown, overlap and actual final errors."""
    from campaign_generate_current_files import official_cut
    from campaign_scoped_tolerance import policy_for
    policy = policy_for(campaign, start, end, rate, target, continuous_rule_sha=continuous_rule_sha)
    index = {}
    for offer in offers:
        if not (offer['start'] <= end and start <= offer['end']) or offer['offer_id'] == excluding_offer:
            continue
        for item in offer['items']:
            if item['status'] in ('success','unknown'):
                index.setdefault(item['item'], []).append((offer,item['status']))
    planned_by_pair = {(r['item'],r['sku']):r for r in planned}
    checks = {(r['item'],r['sku']):r for r in activity}
    for row in planned:
        checks.setdefault((row['item'],row['sku']),dict(row,activity_price=row['daily'],big_target=row.get('big_target',row['target'])))
    reuse, issues, reused_pairs = [], [], set()
    for pair,row in checks.items():
        candidates = index.get(pair[0], [])
        if not candidates:
            if not row['custom'] and pair not in planned_by_pair:
                daily = Decimal(row['activity_price'])
                if daily - official_cut(daily, rate) != Decimal(row['target']):
                    issues.append(dict(item=pair[0],sku=pair[1],error='required_discount_missing_from_selected_scope'))
            continue
        if len(candidates) != 1:
            issues.append(dict(item=pair[0],sku=pair[1],error='multiple_overlapping_discount_evidence'))
            continue
        offer,status = candidates[0]
        base = dict(item=pair[0],sku=pair[1],offer_id=offer['offer_id'],evidence_kind=offer.get('evidence_kind','registered_terminal'))
        actual = [r for r in offer['rows'] if (r['item'],r['sku']) == pair]
        # A partial *ordinary* import does not create a discount requirement for
        # an unsubmitted custom SKU. Revalidate the original failed complement
        # and full successful readback; an unverified unknown still stays held.
        if row['custom'] and not actual and offer.get('partial_terminal_evidence'):
            from campaign_partial_discount import verified_rows
            # Later verified amendments change amounts, not which custom rows
            # were absent from the original import. Verify original amounts
            # against the original receipt, never against the amended values.
            original=offer.get('verified_partial_original_rows',offer['rows'])
            verify_partial_added_scope(offer,original)
            verified_rows(dict(offer,rows=original),offer['partial_terminal_evidence'])
            continue
        if status != 'success' and pair not in set(map(tuple,offer.get('verified_partial_skus',[]))):
            issues.append(dict(base,error='existing_discount_outcome_unknown'));continue
        if (offer['start'],offer['end']) != (start,end):
            issues.append(dict(base,error='existing_discount_window_not_exact'));continue
        if row['custom']:
            if actual:issues.append(dict(base,error='custom_sku_has_existing_discount_requires_review'))
            continue
        if len(actual) != 1:
            issues.append(dict(base,error='existing_discount_sku_missing_or_duplicate'));continue
        deduct = Decimal(str(actual[0]['deduct']))
        if not deduct.is_finite() or deduct <= 0 or deduct != deduct.quantize(Decimal('.01')):
            issues.append(dict(base,error='existing_discount_amount_invalid'));continue
        daily = Decimal(row['activity_price'])
        target_price,big = Decimal(row['target']),Decimal(row['big_target'])
        if any(not x.is_finite() or x <= 0 or x != x.quantize(Decimal('.01')) for x in (daily,target_price,big)) or target_price < big:
            issues.append(dict(base,error='existing_discount_price_input_invalid'));continue
        cut = official_cut(daily,rate)
        final = daily-cut-deduct
        delta = final-target_price
        detail = dict(base,actual_deduct=str(deduct),ideal_deduct=str(daily-cut-target_price),official_cut=str(cut),
                      final=str(final),target=str(target_price),big_target=str(big),delta=str(delta))
        if policy:detail['final_price_tolerance'] = policy
        if actual[0].get('amendment_receipt'):detail['amendment_receipt']=actual[0]['amendment_receipt']
        if final <= 0:issues.append(dict(detail,error='actual_reused_discount_final_nonpositive'))
        elif policy and abs(delta) > Decimal(policy['max_absolute_delta_cny']):
            issues.append(dict(detail,error='actual_reused_discount_outside_scoped_tolerance'))
        elif not policy and final < big:issues.append(dict(detail,error='actual_reused_discount_final_below_big_floor'))
        elif not policy and final != target_price:issues.append(dict(detail,error='actual_reused_discount_final_not_frozen_target'))
        else:
            reuse.append(detail);reused_pairs.add(pair)
    return [r for r in planned if (r['item'],r['sku']) not in reused_pairs], reuse, issues
