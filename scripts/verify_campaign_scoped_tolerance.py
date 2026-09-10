"""Read-only saved-assembly comparison. Temporary ledger; never claims or uploads."""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from campaign_entry_authority import Authority, file_sha, load
from campaign_discount_reuse import reconcile
from campaign_scoped_tolerance import SCOPE, policy_for


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assembly',type=Path,required=True)
    args=p.parse_args();assembly=load(args.assembly)
    with tempfile.TemporaryDirectory() as temp:
        a=Authority(Path(temp)/'isolated.sqlite3')
        try:
            activity,planned=assembly['activity_rows'],assembly['expected_discount_rows']
            offers=a.discount_offers()
            _,_,before=reconcile(activity,planned,offers,SCOPE[1],SCOPE[2],SCOPE[3])
            new,reuse,after=reconcile(activity,planned,offers,SCOPE[1],SCOPE[2],SCOPE[3],campaign=SCOPE[0],target=SCOPE[4])
            accepted=[r for r in reuse if Decimal(r['delta'])!=0]
            output=dict(assembly_path=str(args.assembly),assembly_sha256=file_sha(args.assembly),
                policy=policy_for(*SCOPE),platform_write=False,production_claims_written=0,
                erp_or_discount_writes=False,before_errors=dict(Counter(r['error'] for r in before)),
                after_errors=dict(Counter(r['error'] for r in after)),reused_rows=len(reuse),
                accepted_nonzero_rows=len(accepted),accepted_nonzero_items=len({r['item'] for r in accepted}),
                minimum_delta=str(min(Decimal(r['delta']) for r in accepted)) if accepted else None,
                maximum_delta=str(max(Decimal(r['delta']) for r in accepted)) if accepted else None,
                remaining_new_discount_rows=len(new),
                note='Saved full assembly diagnosis only. Current success exclusions remain the live generation owner responsibility; no batch generated.')
            print(json.dumps(output,ensure_ascii=False,indent=2))
        finally:a.close()


if __name__=='__main__':main()
