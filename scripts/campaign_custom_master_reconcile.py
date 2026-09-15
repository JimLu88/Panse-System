"""One user-authorized custom master repair. No browser or campaign submission.

The executor owns --apply. A lost response is reconciled with --readback, never
by a blind repeat. Exact existing physical IDs are retained; no SKU is created.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

ITEM = '1001358847694'
CODE = 'PPS' + ITEM
AUDIT = '/campaign/custom-master/20260915/' + ITEM
SOURCE_SHA = '563ea206062c7922209a491d792797c25fd93a9ce0381bc732e0e705019c1d49'
# This is an exact one-time authorization, not a new pricing policy.
ROWS = (
    ('5991470332907', CODE+'99', '小件家具定制', '2000.00', '樱桃木家具定制', '20000.00'),
    ('5991470332908', CODE+'98', '中件家具定制', '5000.00', '黑胡桃木家具定制', '20000.00'),
    ('6001467129246', CODE+'97', '大件家具定制', '9000.00', '榉木家具定制', '20000.00'),
    ('6231821270898', CODE+'96', '超大件家具定制', '13000.00', '餐桌定制', '5000.00'),
    ('6001467129247', CODE+'95', '其他定制', '20000.00', '其他定制', '10000.00'),
)


def validate_scope(scope):
    if scope.get('complete') is not True:
        raise ValueError('official_export_incomplete')
    on_sale = [r for r in scope['platform_rows'] if str(r.get('item')) == ITEM]
    if len(on_sale) != 1 or on_sale[0].get('on_sale') is not True:
        raise ValueError('exact_on_sale_item_required')
    actual = [r for r in scope['sku_facts'] if str(r.get('facts', {}).get('item')) == ITEM]
    if len(actual) != 5:
        raise ValueError('exact_five_platform_skus_required')
    by_id = {str(r['facts']['sku']): r for r in actual}
    if len(by_id) != 5:
        raise ValueError('duplicate_physical_sku')
    for sku, code, name, price, _, _ in ROWS:
        row = by_id.get(sku) or {}
        f = row.get('facts') or {}
        if (f.get('attributes') != '颜色分类:'+name+';'
                or Decimal(str(f.get('price', '-1'))) != Decimal(price)
                or f.get('sku_code') not in ('', None)
                or not any(s.get('sha256') == SOURCE_SHA for s in row.get('sources', []))):
            raise ValueError('approved_platform_evidence_changed:' + sku)
    return [dict(sku=s, code=c, name=n, price=p, old_name=on, old_price=op)
            for s, c, n, p, on, op in ROWS]


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def state_sql():
    return f"""SELECT json_build_object(
      'products',(SELECT coalesce(json_agg(row_to_json(x)), '[]') FROM
         (SELECT id,code,name,taobao_id,listing_status FROM products
          WHERE code={literal(CODE)} OR taobao_id={literal(ITEM)}
             OR coalesce(alt_taobao_ids::jsonb,'[]'::jsonb) ? {literal(ITEM)}) x),
      'rows',(SELECT coalesce(json_agg(row_to_json(x)), '[]') FROM
         (SELECT s.id,s.product_code,s.sku_code,s.sku,s.daily_price::text,
                 s.is_custom_placeholder,p.taobao_item_id,p.taobao_sku_id
          FROM pricing_sku s JOIN pricing_sku_promo p USING(sku_code)
          WHERE s.product_code={literal(CODE)} ORDER BY s.sku_code) x),
      'receipts',(SELECT coalesce(json_agg(request_body),'[]') FROM audit_logs
                  WHERE path={literal(AUDIT)} AND status_code=200));"""


def build_sql(rows, evidence_sha):
    # PostgreSQL locks/CAS and the audit commit together. A transaction error
    # rolls back the new master and all five prices, not just the last row.
    payload = json.dumps(dict(authorization='user-20260915-custom-master-taobao-price',
        item=ITEM, product_code=CODE, platform_scope_sha256=evidence_sha,
        official_source_sha256=SOURCE_SHA, rows=rows,
        baseline_rebased=False, sku_created=False, platform_write=False), ensure_ascii=False)
    incoming = json.dumps(rows, ensure_ascii=False)
    return f"""BEGIN ISOLATION LEVEL SERIALIZABLE;
SET LOCAL statement_timeout='25s'; SET LOCAL lock_timeout='5s';
SELECT pg_advisory_xact_lock(hashtext({literal(AUDIT)}));
CREATE TEMP TABLE approved_custom_rows ON COMMIT DROP AS
 SELECT * FROM jsonb_to_recordset({literal(incoming)}::jsonb)
 AS t(sku text,code text,name text,price numeric,old_name text,old_price numeric);
DO $repair$
DECLARE before_rows json; prior_count integer;
BEGIN
 SELECT count(*) INTO prior_count FROM audit_logs WHERE path={literal(AUDIT)} AND status_code=200;
 IF prior_count > 1 THEN RAISE EXCEPTION 'duplicate_repair_receipt'; END IF;
 PERFORM 1 FROM pricing_sku WHERE product_code={literal(CODE)} FOR UPDATE;
 PERFORM 1 FROM pricing_sku_promo WHERE sku_code IN (SELECT code FROM approved_custom_rows) FOR UPDATE;
 IF (SELECT count(*) FROM pricing_sku WHERE product_code={literal(CODE)}) <> 5
 OR (SELECT count(*) FROM pricing_sku s JOIN pricing_sku_promo p USING(sku_code)
     JOIN approved_custom_rows t ON s.sku_code=t.code AND p.taobao_sku_id=t.sku
     WHERE s.product_code={literal(CODE)} AND p.taobao_item_id={literal(ITEM)}
       AND s.is_custom_placeholder=true
       AND coalesce(p.alt_taobao_sku_ids::jsonb,'[]'::jsonb)='[]'::jsonb) <> 5
 OR (SELECT count(*) FROM pricing_sku_promo WHERE taobao_sku_id IN
     (SELECT sku FROM approved_custom_rows)) <> 5
 THEN RAISE EXCEPTION 'exact_existing_five_sku_mapping_changed'; END IF;
 IF prior_count=1 THEN
   IF NOT EXISTS (SELECT 1 FROM audit_logs WHERE path={literal(AUDIT)}
       AND request_body::jsonb->'authorization' = {literal(json.dumps('user-20260915-custom-master-taobao-price'))}::jsonb)
   OR (SELECT count(*) FROM products WHERE code={literal(CODE)} AND taobao_id={literal(ITEM)}
       AND listing_status='在售')<>1
   OR (SELECT count(*) FROM pricing_sku s JOIN approved_custom_rows t ON s.sku_code=t.code
       WHERE s.daily_price=t.price AND s.sku=t.name)<>5
   THEN RAISE EXCEPTION 'committed_repair_drift_no_overwrite'; END IF;
 ELSE
   IF EXISTS (SELECT 1 FROM products WHERE code={literal(CODE)} OR taobao_id={literal(ITEM)}
      OR coalesce(alt_taobao_ids::jsonb,'[]'::jsonb) ? {literal(ITEM)})
   THEN RAISE EXCEPTION 'existing_master_requires_reconciliation'; END IF;
   IF (SELECT count(*) FROM pricing_sku s JOIN approved_custom_rows t ON s.sku_code=t.code
      WHERE s.daily_price=t.old_price AND s.sku=t.old_name)<>5
   THEN RAISE EXCEPTION 'old_prices_or_names_changed_no_overwrite'; END IF;
   SELECT json_agg(row_to_json(s)) INTO before_rows FROM pricing_sku s WHERE product_code={literal(CODE)};
   INSERT INTO products(code,name,taobao_id,listing_status,priority,alt_taobao_ids,semi_finished_eligible)
      VALUES ({literal(CODE)},'家具定制',{literal(ITEM)},'在售','mid','[]',false);
   UPDATE pricing_sku s SET sku=t.name,daily_price=t.price,updated_at=now()
      FROM approved_custom_rows t WHERE s.sku_code=t.code;
   INSERT INTO audit_logs(username,method,path,status_code,request_body,note)
     VALUES ('campaign-executor','CLI',{literal(AUDIT)},200,
       ({literal(payload)}::jsonb || jsonb_build_object('before_prices',before_rows))::json,
       'User authorized exact ERP master creation and Taobao daily prices; no baseline or platform mutation');
 END IF;
END $repair$;
COMMIT;
BEGIN READ ONLY;
{state_sql()}
COMMIT;
"""


def readback_verified(state):
    masters = state.get('products') or []
    if len(masters) != 1 or any(masters[0].get(k) != v for k, v in
        dict(code=CODE,taobao_id=ITEM,listing_status='在售').items()):
        return False
    rows = state.get('rows') or []
    if len(rows) != 5 or len(state.get('receipts') or []) != 1:
        return False
    receipt=state['receipts'][0]
    if (receipt.get('authorization') != 'user-20260915-custom-master-taobao-price'
            or receipt.get('official_source_sha256') != SOURCE_SHA):
        return False
    mapped = {r['sku_code']: r for r in rows}
    return all(c in mapped and mapped[c]['taobao_sku_id'] == s
        and mapped[c]['taobao_item_id'] == ITEM and mapped[c]['sku'] == n
        and Decimal(mapped[c]['daily_price']) == Decimal(p)
        and mapped[c]['is_custom_placeholder'] is True for s,c,n,p,_,_ in ROWS)


def run_sql(sql):
    remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-db-1 psql -X -v ON_ERROR_STOP=1 -U panse -d panse_erp -At'
    result = subprocess.run(['C:/Program Files/Git/usr/bin/ssh.exe', '-i',
        str(Path.home()/'.ssh/panse_nas'), '-o','BatchMode=yes','-o','ConnectTimeout=15',
        '-p','2222','15068803006@DS923plus',remote], input=sql, capture_output=True,
        text=True,encoding='utf-8',timeout=45,check=True)
    docs = [json.loads(x) for x in result.stdout.splitlines() if x.startswith('{')]
    if len(docs) != 1:
        raise ValueError('repair_readback_missing_do_not_repeat_write')
    return docs[0]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scope', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--apply',action='store_true')
    modes.add_argument('--readback',action='store_true')
    modes.add_argument('--plan',action='store_true')
    a=p.parse_args()
    if a.output.exists(): raise ValueError('receipt_exists_no_overwrite')
    raw=a.scope.read_bytes();rows=validate_scope(json.loads(raw.decode('utf-8-sig')))
    if a.plan:
        result=dict(status='prepared_not_executed',item=ITEM,rows=rows)
    else:
        if a.apply:
            from campaign_entry_authority import Authority
            authority=Authority()
            try:
                snapshot={'all_erp_rows':[dict(item=ITEM,sku=r['sku'],code=r['code'],
                    product_code=CODE,alt=[],custom=True,daily=r['old_price']) for r in rows]}
                bases=authority.bases(snapshot)
                for r in rows:
                    basis=bases.get((ITEM,r['sku']))
                    if basis and (basis.get('uncertain') or Decimal(r['price']) < Decimal(basis['floor'])):
                        raise ValueError('fixed_original_floor_conflict:'+r['sku'])
            finally: authority.close()
        sql=build_sql(rows,hashlib.sha256(raw).hexdigest()) if a.apply else 'BEGIN READ ONLY;'+state_sql()+'COMMIT;'
        state=run_sql(sql)
        result=dict(status='erp_reconciled' if readback_verified(state) else 'readback_not_verified',
                    item=ITEM,platform_write=False,baseline_rebased=False,state=state)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('state','rows')},ensure_ascii=False))
    if result['status']=='readback_not_verified':raise SystemExit(2)


if __name__=='__main__': main()
