"""Read-only deployed catalog and static UI proof; no business data or sends."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--commit', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
base = 'http://192.168.31.21:8200'
ssh = ['ssh', '-i', 'C:/Users/lzdwy/.ssh/panse_nas', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=20', '-p', '2222', '15068803006@DS923plus']


def get(path):
    with urllib.request.urlopen(base + path, timeout=20) as response:
        return json.loads(response.read())


code = """
import json
from app.services.business_relationship_service import snapshot, impact
s=snapshot()
print(json.dumps({'version':s['version'], 'nodes':len(s['nodes']),
'edges':len(s['edges']), 'flows':len(s['flows']),
'node_ids':[n['id'] for n in s['nodes']],
'faults':[ref for item in s['nodes']+s['edges'] for ref in item['sources'] if ref['state']!='matching'],
'validation_errors':s['validation_errors'], 'runtime_verified':s['runtime_verified'],
'read_only':s['read_only'], 'password_downstream':[n['id'] for n in impact(['sync.password'],data=s)['nodes']]}))
"""
result = subprocess.run(ssh + ['sudo /usr/local/bin/docker exec panse-system-api-1 python -c ' + shlex.quote(code)],
                        check=True, capture_output=True, text=True, encoding='utf-8')
catalog = json.loads(result.stdout)
probe = "grep -rl '活动程序全链路' /usr/share/nginx/html/assets"
result = subprocess.run(ssh + ['sudo /usr/local/bin/docker exec panse-system-web-1 sh -c ' + shlex.quote(probe)],
                        check=True, capture_output=True, text=True, encoding='utf-8')
visible = False
for asset in result.stdout.splitlines():
    if not asset.endswith('.js'):
        continue
    with urllib.request.urlopen(base + '/assets/' + Path(asset).name, timeout=20) as response:
        content = response.read().decode('utf-8')
    visible |= all(word in content for word in ('活动相关关系', '飞书相关关系', '工厂表同步流程'))
api, web, health = get('/api/version'), get('/build-version.json'), get('/api/health')
passed = (api.get('commit_full') == args.commit and args.commit in json.dumps(web)
          and health.get('ok') and visible and catalog['read_only']
          and not catalog['runtime_verified'] and not catalog['faults'] and not catalog['validation_errors']
          and {'campaign.controller', 'campaign.failure', 'sync.campaign_terminal', 'sync.conflict', 'sync.password'} <= set(catalog['node_ids'])
          and 'sync.readback' in catalog['password_downstream'])
receipt = {'passed':bool(passed), 'api':api, 'web':web, 'health':health, 'catalog':catalog,
           'published_shortcuts':visible, 'business_operations':0,
           'scope':'deployment and relationship metadata only; not enrollment or Feishu delivery acceptance'}
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(receipt, ensure_ascii=False))
raise SystemExit(0 if passed else 1)
