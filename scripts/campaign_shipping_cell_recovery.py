"""Restore an accidentally filled optional cell to the exact official template."""
from pathlib import Path
import hashlib
from campaign_official_template import _archive, _read, _effective, sheet_path


def cells(ref, item):
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:
        raise ValueError('shipping_cell_source_changed')
    with _archive(raw) as archive:
        _,_,rows,merges=_read(archive,sheet_path(archive,'商品SKU导入列表'))
        if rows.get(2,{}).get('R')!='发货时间':
            raise ValueError('shipping_cell_header_changed')
        result={}
        for n,row in rows.items():
            if n<4 or not row.get('E') or _effective(rows,merges,n,'A')!=item:continue
            if row['E'] in result:raise ValueError('duplicate_shipping_sku')
            result[row['E']]=_effective(rows,merges,n,'R')
        return result


def validate(proof, item, expected_skus):
    if proof.get('item')!=item or proof.get('skus')!=sorted(expected_skus):
        raise ValueError('shipping_cell_scope_changed')
    native=cells(proof['template'],item);sent=cells(proof['submitted'],item)
    if not expected_skus or set(sent)!=set(expected_skus) or not set(sent).issubset(native):
        raise ValueError('shipping_cell_scope_not_complete')
    if any(native[s]!='' or not sent[s] for s in sent):
        raise ValueError('shipping_cell_not_generated_from_blank')
    return True


def annotate(errors, body):
    output=[]
    for error in errors:
        if (error.get('message')!='商品无须设置发货时间' or error.get('sku')
                or error.get('terminal')!='failed' or not error.get('official_evidence')):
            output.append(error);continue
        item=error['item'];skus=sorted(r['sku'] for r in body['signup_rows'] if r['item']==item)
        candidates=[r for r in body['files'] if Path(r['path']).name=='活动报名.xlsx']
        if len(candidates)!=1:output.append(error);continue
        proof={'item':item,'skus':skus,'template':{'path':body['template_path'],'sha256':body['template_sha256']},
               'submitted':candidates[0]}
        try:validate(proof,item,skus)
        except ValueError:output.append(error);continue
        output.append(dict(error,kind='file_template_shipping_blank',
            parse_issue='unnecessary_shipping_time_generated',shipping_cell_proof=proof))
    return output
