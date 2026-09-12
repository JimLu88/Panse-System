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
    if '--legacy-cloud-aliases' in sys.argv:
        from campaign_entry_authority import file_sha
        from campaign_catalog_repair import mapped_rows
        original=Path(__file__).resolve().parents[1]/'docs/receipts/campaign-scoped-catalog-repair-20260912.json'
        doc=load(original);snapshot_path=SEG/'resolved-snapshot.json'
        authority=Authority()
        try:snapshot=authority.resolve_snapshot(load(snapshot_path))
        finally:authority.close()
        terminal_path=ROOT/'Web-Agent程序/data/output/campaign-transfers/9dcd8d27aa0c659f2c6cd4c283cfc11f83c492f56ead629a5529f4284283f02e/created-discount-terminal.json'
        rejected={(r['item'],r['sku']) for r in load(terminal_path)['failure_rows']}
        facts=load(doc['official_scope_path'])['scope']['sku_facts'];restored=[]
        for entry in facts:
            f=entry['facts']
            if f['item']!='720234422814' or not f['sku_code'].isdigit():continue
            if (f['item'],f['sku']) in rejected:continue
            candidates=[r for r in snapshot['all_erp_rows'] if '720234422814' in item_ids(r)
                and f['sku'] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
            if len(candidates)!=1:continue
            r=candidates[0]
            if r['code']!='PPS'+f['sku_code'] or r.get('custom') is not False or r.get('sku_name')!=f['attributes']:
                raise ValueError('cloud_legacy_code_or_spec_conflict')
            restored.append(dict(item=f['item'],sku=f['sku'],erp_code=r['code'],official_sku_code=f['sku_code'],
                                 repair_kind='verified_bound_legacy_prefix'))
        if len(restored)!=36:raise ValueError('exact_36_cloud_aliases_required')
        doc.update(retired=[],restored=restored,user_verbatim='14件：商品与ERP映射缺口。你进行修复，其实等于原因不明，需要你排查',
                   scope='Exact already-bound physical SKU and same specification only; no price, stock or platform edits.')
        doc['sources'].append(dict(path=str(snapshot_path),sha256=file_sha(snapshot_path)))
        doc['sources'].append(dict(path=str(terminal_path),sha256=file_sha(terminal_path)))
        path=original.with_name('campaign-cloud36-legacy-code-aliases-20260912.json')
        with path.open('x',encoding='utf-8',newline='\n') as stream:json.dump(doc,stream,ensure_ascii=False,indent=2)
        ref=dict(path=str(path),sha256=file_sha(path));snapshot['catalog_repair_sources']=[ref]
        if mapped_rows(snapshot,snapshot['all_erp_rows'])!=snapshot['all_erp_rows']:raise ValueError('legacy_alias_changed_mapping_or_price')
        print(json.dumps(ref,ensure_ascii=False))
    elif '--current-mapping-summary' in sys.argv:
        import sqlite3
        from campaign_catalog_repair import remaining_mapping_summary
        authority=Authority()
        try:snapshot=authority.resolve_snapshot(load(SEG/'resolved-snapshot.json'))
        finally:authority.close()
        db=sqlite3.connect((RUN/'controller.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
        try:
            states=[json.loads(r[0]) for r in db.execute('SELECT body FROM continuous_campaign_runs')]
        finally:db.close()
        matches=[s for s in states if any(d.get('reason')=='erp_mapping_missing_or_not_unique' for ds in s.get('exceptions',{}).values() for d in ds)]
        if len(matches)!=1:raise ValueError('exact_mapping_exception_run_required')
        summary=remaining_mapping_summary(snapshot,matches[0]['exceptions'])
        target=ROOT/'outputs/01a03341-b2cd-7810-92f3-66fad189521d/mapping11-current-summary-20260912.json'
        with target.open('x',encoding='utf-8') as stream:json.dump(summary,stream,ensure_ascii=False,indent=2)
        print(json.dumps(dict(path=str(target),historical=summary['historical_sku_count'],remaining=summary['remaining_sku_count']),ensure_ascii=False))
    elif '--live-erp' in sys.argv:
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
