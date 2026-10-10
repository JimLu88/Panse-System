"""Register an already verified local receipt; no platform action or source scan."""
import argparse
from campaign_entry_authority import Authority

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind',choices=('fixed','mapping','rotation','outcome','discount','catalog'),required=True)
    p.add_argument('--receipt',required=True)
    p.add_argument('--sha256',required=True)
    args=p.parse_args()
    authority=Authority()
    try:
        authority.register_source(args.receipt,args.kind,args.sha256)
        print('registered_local_receipt_no_platform_write')
    finally:authority.close()

if __name__=='__main__':main()
