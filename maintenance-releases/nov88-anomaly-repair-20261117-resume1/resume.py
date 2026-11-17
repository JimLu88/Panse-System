"""One fixed continuation: excludes the confirmed first item, no original run replay."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import repair_core as c
sys.path[:0]=[str(c.B/'Web-Agent程序'),str(c.B/'ERP程序/scripts')]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['status','verify','run'])
    args=parser.parse_args()
    from campaign_edge_client import EdgeClient
    from app.vault.store import get_or_create_api_token
    edge=EdgeClient(get_or_create_api_token())
    stage='apply' if args.operation=='run' else args.operation
    options={'request_sha256':c.sha(c.ROOT/'request.json')}
    result=edge._action('program_nov88_anomaly_resume_'+stage,options)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if stage!='apply' or result.get('already_completed_no_replay'): return
    terminal=c.ROOT/'resume-1/apply/terminal.json'; deadline=time.monotonic()+6300
    while not terminal.exists() and time.monotonic()<deadline: time.sleep(2)
    if not terminal.exists(): raise TimeoutError('original_continuation_still_pending_do_not_restart')
    print(json.dumps(c.load(terminal),ensure_ascii=False))

if __name__=='__main__': main()
