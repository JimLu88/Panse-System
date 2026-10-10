"""Audited mapping receipt replacement; original history is preserved."""
import hashlib,json
from collections import Counter
from pathlib import Path

def apply(sources):
    removed=set()
    for current in sources:
        doc=current.get('document') or {}
        old_sha=doc.get('supersedes_mapping_sha256')
        if not old_sha:continue
        if current['kind']!='mapping' or doc.get('status')!='verified_partial_mapping_restored':raise ValueError('mapping supersession type invalid')
        old=[s for s in sources if s['kind']=='mapping' and s['sha256']==old_sha]
        if len(old)!=1 or old_sha in removed:raise ValueError('mapping predecessor not unique')
        key=lambda r:(r['item'],r['erp_code'])
        old_rows=old[0]['document']['restored'];rows=doc['restored']
        if Counter(key(r) for r in rows)!=Counter(key(r) for r in old_rows):raise ValueError('mapping supersession scope changed')
        proof=doc['evidence'];raw=Path(proof['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=proof['sha256']:raise ValueError('mapping proof changed')
        evidence=json.loads(raw)
        if evidence.get('state')!='rotation_mapping_verified' or evidence.get('erp_prices_unchanged') is not True:raise ValueError('mapping not verified')
        mapped={r['sku_code']:str(r['taobao_sku_id']) for r in evidence['readback']['mappings']}
        if len([r for r in rows if r['erp_code'] in mapped])!=len(mapped):raise ValueError('corrected mapping not unique')
        if {r['erp_code']:r['sku'] for r in rows if r['erp_code'] in mapped}!=mapped:raise ValueError('mapping rows disagree with applied readback')
        if [r for r in rows if r['erp_code'] not in mapped]!=[r for r in old_rows if r['erp_code'] not in mapped]:raise ValueError('unrelated mapping changed')
        removed.add(old_sha)
    return [s for s in sources if s['sha256'] not in removed]
