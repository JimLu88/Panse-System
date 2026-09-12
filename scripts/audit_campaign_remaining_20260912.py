"""Read-only diagnosis of the 25-item closeout; no platform or claim writes."""
import json
from pathlib import Path
from campaign_entry_authority import Authority, load

ROOT = Path('D:/AI/畔色ERP系统')
RUN = ROOT/'outputs/campaign-continuous/runs/2c8df6bc7fb7e73bbf8425ef135806d21454c676e0cac437cf9fd5af32ada110'
SEG = RUN/'segments/bd2877766c31b1fa89ac5c095e63fbbb5a9123f298ec041e0aab8f24fe5d0fd7'
FINAL = ROOT/'outputs/01a067c6-7e83-7483-9a21-84b44ed7299b/campaign-scope57-final-checkpoint-20260912.json'

def item_ids(r):
    return {str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}

def inspect():
    authority=Authority()
    try:
        snapshot=authority.resolve_snapshot(load(SEG/'resolved-snapshot.json'))
    finally:
        authority.close()
    rows=snapshot['all_erp_rows']
    scope_paths=sorted((SEG/'repairs').glob('scope-*.json'))
    scope=load(scope_paths[-1])['scope'] if scope_paths else load(SEG/'product-scope.json')
    facts={(e['facts']['item'],e['facts']['sku']):e['facts'] for e in scope['sku_facts']}
    result=[]
    for entry in load(FINAL)['remaining']:
        item=entry['item']; local=[r for r in rows if item in item_ids(r)]
        detail=[]
        for issue in entry['issues']:
            sku=issue.get('sku'); fact=facts.get((item,sku),{})
            code=fact.get('sku_code')
            bound=[r for r in local if sku in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
            coded=[r for r in rows if code and r['code']==code]
            detail.append(dict(sku=sku, fact=fact,
                bound=[{k:r.get(k) for k in ('code','daily','sku_name','custom','product_name')} for r in bound],
                code_matches=[{k:r.get(k) for k in ('code','item','product_item_id','daily','sku_name','product_name','listing_status')} for r in coded]))
        result.append(dict(item=item,category=entry['category'],erp_product_names=sorted({r['product_name'] for r in local if r.get('product_name')}),
            erp_rows=len(local),issues=detail))
    return result

if __name__=='__main__':
    import sys
    if '--live-erp' in sys.argv:
        from campaign_price_snapshot import load_rows,build_snapshot
        path=ROOT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/remaining25-current-erp-20260912.json'
        with path.open('x',encoding='utf-8') as stream:
            json.dump(build_snapshot(load_rows()),stream,ensure_ascii=False,indent=2)
        print(str(path))
    elif '--live-identities' in sys.argv:
        import campaign_price_snapshot as ps
        ids=sorted({i['sku'] for e in inspect() for i in e['issues'] if i.get('sku')})
        if not all(s.isdigit() for s in ids):raise ValueError('invalid scoped sku')
        selected=','.join("'"+s+"'" for s in ids)
        ps.SQL="""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='20s';
SELECT row_to_json(q) FROM (SELECT 'delisted' AS code,value_plain FROM system_settings WHERE key='delisted_skuids' AND is_secret=false) q;
SELECT row_to_json(q) FROM (SELECT 'listing:'||id AS code,taobao_item_id,taobao_sku_id,sku_code,merchant_code,sku_spec,matched,updated_at FROM taobao_listings WHERE taobao_sku_id IN ("""+selected+""")) q;
SELECT row_to_json(q) FROM (SELECT 'identity:'||id AS code,taobao_item_id,taobao_sku_id,sku_code,merchant_code,sku_spec,latest_sale_state,latest_evidence_source,latest_evidence_sha256,conflict_detected FROM sku_identities WHERE taobao_sku_id IN ("""+selected+""")) q;
COMMIT;"""
        path=ROOT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/remaining25-current-identities-20260912.json'
        result=ps.load_rows()
        with path.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2,default=str)
        retired=set(json.loads(next(r['value_plain'] for r in result if r['code']=='delisted')))
        for e in inspect():
            if e['category']=='erp_mapping':print(e['item'],'retired',[i['sku'] for i in e['issues'] if i['sku'] in retired],'other',[i['sku'] for i in e['issues'] if i['sku'] not in retired])
        print('saved',path)
    elif '--catalog-receipt' in sys.argv:
        from campaign_entry_authority import file_sha
        registry=ROOT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/remaining25-current-identities-20260912.json'
        disabled=ROOT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/backup9/remaining-current-checkpoint-20260905.json'
        official=sorted((SEG/'repairs').glob('scope-*.json'))[-1]
        snapshot=load(SEG/'resolved-snapshot.json');data=inspect()
        retired=set(json.loads(next(r['value_plain'] for r in load(registry) if r['code']=='delisted')))
        skipped=[dict(item=e['item'],sku=i['sku']) for e in data if e['category']=='erp_mapping' for i in e['issues'] if i['sku'] in retired]
        old=load(disabled)['unused_backup_switches_off']
        doc=dict(schema='campaign_scoped_catalog_repair_v1',
            user_verbatim='14件：商品与ERP映射缺口。你进行修复，其实等于原因不明，需要你排查',
            snapshot_captured_at=snapshot['captured_at'],price_version=snapshot['resolved_price_version_sha256'],
            registry_path=str(registry),official_scope_path=str(official),
            sources=[dict(path=str(p),sha256=file_sha(p)) for p in (registry,disabled,official)],
            retired=skipped,historical_disabled_not_applied_without_current_confirmation=old['skus'],
            restored=[dict(item='720234422814',sku='6135325229595',erp_code='PPS2316001040302',official_sku_code='PPS2316001040302|',repair_kind='verified_exact_trailing_delimiter')],
            scope='Only this unchanged product snapshot. No listing switch changes, no retired identity restoration, no price changes.')
        path=Path(__file__).resolve().parents[1]/'docs/receipts/campaign-scoped-catalog-repair-20260912.json'
        with path.open('x',encoding='utf-8') as stream:json.dump(doc,stream,ensure_ascii=False,indent=2)
        print(json.dumps(dict(path=str(path),retired=len(skipped),historical_disabled_not_applied=len(old['skus']),mapped=1)))
    else:
        print(json.dumps(inspect(),ensure_ascii=False,indent=2))
