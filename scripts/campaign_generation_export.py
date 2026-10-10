"""Per-batch complete export binding. Does not replace the shared fact index."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path('D:/AI/畔色ERP系统/Web-Agent程序/data/output/campaign-transfers')


def read_export(reference):
    path = Path(reference['path'])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('generation_product_export_changed')
    job = json.loads(raw.decode('utf-8-sig'))
    from campaign_product_scope import from_edge_job
    scope = from_edge_job(job, expected_request_id=job['result']['snapshot_request_id'],
                          expected_shop='畔色木作', roots=[ROOT])
    from campaign_sku_fact_store import parse
    facts = []
    for source in job['result']['files']:
        raw = Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['sha256']:
            raise ValueError('generation_export_file_changed')
        facts.extend(dict(row, export_sha256=source['sha256'])
                     for row in parse(raw) if row['sku'])
    if len({(r['item'],r['sku']) for r in facts}) != len(facts):
        raise ValueError('generation_export_duplicate_pair')
    return scope, facts


def bind_rows(rows, facts):
    """Exact unique ERP code within the same product; never alter prices/primary IDs."""
    result = deepcopy(rows)
    for fact in facts:
        item, sku, code = fact['item'], fact['sku'], fact['merchant_code'].strip()
        related = [r for r in result if item in {str(r.get('item')), str(r.get('product_item_id')),
                   *map(str,r.get('product_alt_item_ids') or [])}]
        if any(sku in {str(r.get('sku')),*map(str,r.get('alt') or [])} for r in related):
            continue  # The full resolver still checks existing binding conflicts and B1 proofs.
        matched = [r for r in related if code and r['code']==code]
        if len(matched)==1:
            matched[0]['alt']=list(dict.fromkeys([*map(str,matched[0].get('alt') or []),sku]))
    return result
