"""Read current registered 78+4 fixed bases. No price, platform or claim writes."""
from decimal import Decimal, ROUND_CEILING
import json
from campaign_entry_authority import Authority, ROOT, load, file_sha, validate_price
from campaign_price_snapshot import build_snapshot


def main():
    path=ROOT/'docs/receipts/campaign-78-custom-user-established-baseline-20260911-v2.json'
    prior_path=ROOT/'docs/receipts/campaign-four-custom-user-established-baseline-20260911.json'
    current,prior=load(path),load(prior_path)
    observed=load(current['baseline_source'])
    facts={(r['item'],r['sku'],r['erp_code']):r for r in observed['rows']}
    assert len(current['rows'])==len(facts)==78
    for row in current['rows']:
        assert Decimal(row['fixed_original_record'])==Decimal(facts[(row['item'],row['sku'],row['erp_code'])]['current_daily'])
    rows=current['rows']+prior['rows']
    snapshot_rows=[dict(item=r['item'],sku=r['sku'],code=r['erp_code'],daily=r['fixed_original_record'],custom=True,alt=[]) for r in rows]
    a=Authority()
    try:
        before_claims=[tuple(r) for r in a.db.execute('SELECT * FROM attempts ORDER BY id')]
        bases=a.bases(build_snapshot(snapshot_rows));assert len(bases)==82
        fractional=[]
        for r in snapshot_rows:
            b=bases[(r['item'],r['sku'])];assert not b['uncertain']
            original,floor=Decimal(b['original']),Decimal(b['floor'])
            assert original==Decimal(r['daily']) and floor==original*Decimal('.20')
            minimum=floor.quantize(Decimal('.01'),rounding=ROUND_CEILING)
            validate_price(dict(custom=True,activity_price=str(minimum)),r['daily'],b,lowering_authorized=True,failed_exact=True)
            try:validate_price(dict(custom=True,activity_price=str(minimum-Decimal('.01'))),r['daily'],b,lowering_authorized=True,failed_exact=True)
            except ValueError:pass
            else:raise AssertionError('below fixed floor accepted')
            if minimum!=floor:fractional.append(dict(item=r['item'],sku=r['sku'],original=str(original),exact_floor=str(floor),minimum_submit_price=str(minimum)))
            r['daily']=str(minimum)
        assert a.bases(build_snapshot(snapshot_rows))==bases
        assert before_claims==[tuple(r) for r in a.db.execute('SELECT * FROM attempts ORDER BY id')]
        print(json.dumps(dict(status='verified',current_bases=78,prior_four_preserved=4,minimum_cent_allowed=82,
            below_minimum_cent_rejected=82,lowered_current_daily_did_not_rebase=True,
            fractional=fractional,source_sha256=file_sha(path),prior_four_sha256=file_sha(prior_path),
            original_authorized_at=current['established_at'],erp_source_observed_at=observed['observed_at'],
            verification='fresh process registered sources and saved exact ERP readback; not a new ERP price query',
            source_corrections=[dict(r) for r in a.db.execute('SELECT old_path,new_path,old_sha256,new_sha256 FROM fixed_source_corrections')],
            active_source_count=len(a.sources()),platform_writes=0,claims_changed=False),ensure_ascii=False,indent=2))
    finally:a.close()


if __name__=='__main__':main()
