"""Separate fixed entry: status / verify / run. Never ordinary enrollment."""
import argparse,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import repair_core as c
sys.path[:0]=[str(c.B/'Web-Agent程序'),str(c.B/'ERP程序/scripts')]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['status','verify','run'])
    args=parser.parse_args();request=c.validate_request()
    from campaign_edge_client import EdgeClient
    from app.vault.store import get_or_create_api_token
    edge=EdgeClient(get_or_create_api_token());options={'request_sha256':c.sha(c.ROOT/'request.json')}
    if args.operation=='run':
        from repair_runtime import checked_plan
        checked_plan(options['request_sha256'])
        release=c.load(c.ROOT/'release.json')
        for ref in release['files']:
            if c.sha(ref['path'])!=ref['sha256']:raise ValueError('repair_release_changed')
        admitted=edge._action('program_nov88_anomaly_apply',options)
        print(json.dumps(admitted,ensure_ascii=False),flush=True)
        terminal=c.ROOT/'apply/terminal.json';deadline=time.monotonic()+2700
        while not terminal.exists() and time.monotonic()<deadline:time.sleep(2)
        if not terminal.exists():raise TimeoutError('original_repair_still_pending_no_replay')
        print(json.dumps(c.load(terminal),ensure_ascii=False));return
    result=edge._action('program_nov88_anomaly_'+args.operation,options)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
