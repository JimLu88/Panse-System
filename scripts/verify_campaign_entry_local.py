"""Read saved sources and replay an existing assembly in an isolated temp ledger.

No business state, source, workbook or platform mutation. JSON summary on stdout.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from campaign_entry_authority import Authority, load
from campaign_discount_reuse import reconcile
from campaign_price_snapshot import digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assembly',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True)
    args=p.parse_args();assembly=load(args.assembly);snapshot=load(args.snapshot)
    with tempfile.TemporaryDirectory() as temp:
        a=Authority(Path(temp)/'test.sqlite3')
        try:
            resolved=a.resolve_snapshot(snapshot)
            bases=a.bases(resolved)
            offers=a.discount_offers()
            offer=next(o for o in offers if o['offer_id']=='144956016253')
            activity=assembly['activity_rows'];planned=assembly['expected_discount_rows']
            _,reused,issues=reconcile(activity,planned,offers,offer['start'],offer['end'],Decimal('.12'))
            below=[i for i in issues if i['error']=='actual_reused_discount_final_below_big_floor']
            rows=resolved['all_erp_rows'];before={r['code']:r.get('daily') for r in snapshot['all_erp_rows']}
            output=dict(platform_write=False,business_database_write=False,production_claims_written=0,
                source_count=len(a.sources()),fixed_basis_pairs=len(bases),
                mapping_overlay_changed_rows=sum(x!=y for x,y in zip(rows,snapshot['all_erp_rows'])),
                current_prices_preserved=all(before[r['code']]==r.get('daily') for r in rows),
                input_price_version=snapshot['resolved_price_version_sha256'],resolved_version=digest(rows),
                protected_autumn_items=sorted(a.blocked('49557/49560/3538210379','signup',offer['start'],offer['end'])),
                actual_discount=dict(id=offer['offer_id'],rows=len(offer['rows']),items=len(offer['items']),kind=offer['evidence_kind']),
                assembly_items=len({r['item'] for r in activity}),assembly_skus=len(activity),
                issue_counts=dict(Counter(i['error'] for i in issues)),exact_reuse_rows=len(reused),
                below_floor_rows=len(below),below_floor_items=len({r['item'] for r in below}),
                minimum_shortfall=str(min(-Decimal(i['delta']) for i in below)) if below else None,
                maximum_shortfall=str(max(-Decimal(i['delta']) for i in below)) if below else None,
                example=below[0] if below else None)
            print(json.dumps(output,ensure_ascii=False,indent=2))
        finally:a.close()


if __name__=='__main__':main()
