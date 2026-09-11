"""Independently verify exact failure rows from the original discount XLSX."""
from xml.etree import ElementTree as ET

from campaign_entry_authority import file_sha
from campaign_official_template import _archive,_read,sheet_path,NS


def verify_failure_rows(path,sha,expected,failed_count):
    if file_sha(path)!=sha:raise ValueError('discount_feedback_hash_changed')
    from pathlib import Path
    aliases={'item':{'商品ID','商品id','商品Id'},'sku':{'SKUID','SKU ID','skuId','sku_id'},
             'message':{'失败原因','错误原因','错误信息'}}
    found=[]
    with _archive(Path(path).read_bytes()) as z:
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        for sheet in wb.findall('s:sheets/s:sheet',NS):
            _,root,physical,_=_read(z,sheet_path(z,sheet.get('name')))
            header=None;rows=[]
            for _,values in sorted(physical.items()):
                values={k:v.strip() for k,v in values.items()}
                matches={key:[c for c,v in values.items() if v in names] for key,names in aliases.items()}
                if all(len(m)==1 for m in matches.values()):
                    if header is not None:raise ValueError('discount_feedback_multiple_headers')
                    header={k:v[0] for k,v in matches.items()};continue
                if header is None or not any(values.values()):continue
                row={k:values.get(c,'') for k,c in header.items()}
                if not all(row[k].isdigit() for k in ('item','sku')) or not row['message']:
                    raise ValueError('discount_feedback_ambiguous_row')
                rows.append(row)
            if header is not None:
                if root.findall('.//s:f',NS):raise ValueError('discount_feedback_formula_not_identity')
                found.append(rows)
    if len(found)!=1:raise ValueError('discount_feedback_sheet_not_unique')
    rows=found[0];pairs=[(r['item'],r['sku']) for r in rows]
    if type(failed_count) is not int or failed_count<1 or len(rows)!=failed_count or len(set(pairs))!=len(pairs) or not set(pairs).issubset(expected):
        raise ValueError('discount_feedback_scope_or_count_mismatch')
    return rows
