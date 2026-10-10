"""Explicit precision repair with retained old evidence; not price/claim mutation."""
import argparse
import json
from campaign_entry_authority import Authority


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old',required=True);p.add_argument('--old-sha256',required=True)
    p.add_argument('--new',required=True);p.add_argument('--new-sha256',required=True)
    args=p.parse_args();a=Authority()
    try:
        result=a.correct_fixed_floor_source(args.old,args.old_sha256,args.new,args.new_sha256)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:a.close()


if __name__=='__main__':main()
