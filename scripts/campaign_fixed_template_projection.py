"""Populate a fixed official master from the single full product export.

The master explicitly calls B:D/F:M reference-only. Never use its historical
SKU membership, prices, or success state as today's facts. This does not edit
products, stock, listing switches, the master, or any non-data package part.
"""
from copy import copy
from io import BytesIO
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile

from campaign_official_template import (_archive, _read, _effective, _layout,
    sheet_path, template_rows, ROW, CELL, REF)


def project(raw, scope, items):
    wanted = set(map(str, items))
    if not wanted or scope.get('complete') is not True or not scope.get('page_evidence'):
        raise ValueError('complete_current_product_scope_required')
    facts = {}
    for entry in scope.get('sku_facts', []):
        fact = entry['facts']; pair = str(fact['item']), str(fact['sku'])
        if pair[0] not in wanted:
            continue
        if not all(x.isdigit() for x in pair) or pair in facts or not entry.get('sources'):
            raise ValueError('current_sku_identity_or_provenance_invalid')
        facts[pair] = fact
    if {i for i,s in facts} != wanted:
        raise ValueError('current_product_export_scope_missing')
    with _archive(raw) as source:
        path = sheet_path(source, '商品SKU导入列表')
        xml, root, cells, merges = _read(source, path)
        layout = _layout(cells.get(2, {}))
        if layout['percent'] != 'X' or layout['last'] != 'Y':
            raise ValueError('fixed_super_reduce_projection_layout_changed')
        if ('docProps/custom.xml' not in source.namelist()
                or not source.read('docProps/custom.xml')):
            raise ValueError('official_custom_properties_required')
        if re.search(r'<(?:conditionalFormatting|dataValidations|autoFilter|tableParts)\b', xml):
            raise ValueError('unsupported_row_bound_features')
        original = {int(m[1]):m[0] for m in ROW.finditer(xml)}
        rows = template_rows(raw)
        if not rows or any(n not in original for n in (1,2,3)):
            raise ValueError('official_row_prototype_missing')
        prototype = original[rows[0]['row']]
        # Preserve only same-product shipping settings, never copy another
        # product's optional media, inventory limits, prices or enrollment ID.
        shipping = {}
        for row in rows:
            item=row['item']; value=_effective(cells,merges,row['row'],'P')
            if item in shipping and shipping[item] != value:
                raise ValueError('fixed_master_shipping_not_unique')
            shipping[item]=value
        new_rows = [original[n] for n in (1,2,3)]
        for number, pair in enumerate(sorted(facts), 4):
            fact=facts[pair]
            values={'A':pair[0],'E':pair[1],'F':str(fact.get('attributes') or ''),
                    'P':shipping.get(pair[0],'')}
            def replace_cell(match):
                column=match[1]
                opening=match[0].split('>',1)[0].rstrip('/')
                opening=re.sub(r'\s+t="[^"]*"','',opening)
                opening=re.sub(r'\br="[A-Z]+\d+"',f'r="{column}{number}"',opening)
                value=values.get(column,'')
                return opening+' t="inlineStr"><is><t>'+escape(value)+'</t></is></c>'
            row=CELL.sub(replace_cell,prototype)
            row=re.sub(r'(<row\b[^>]*\br=")\d+("[^>]*>)',rf'\g<1>{number}\2',row,count=1)
            new_rows.append(row)
        # Keep header merges exactly; data rows are explicit identities so a
        # removed/added SKU can never inherit a neighboring product's anchor.
        header_merges=[]
        for ref in merges:
            match=REF.fullmatch(ref)
            if not match:
                raise ValueError('invalid_merge_range')
            if int(match[2]) <= 3:
                if int(match[4] or match[2]) > 3:
                    raise ValueError('header_data_cross_merge')
                header_merges.append(ref)
        changed,count=re.subn(r'<sheetData>.*?</sheetData>',
            '<sheetData>'+''.join(new_rows)+'</sheetData>',xml,count=1,flags=re.S)
        if count != 1:
            raise ValueError('unsupported_sheet_data_serialization')
        merge_xml='<mergeCells count="'+str(len(header_merges))+'">'+''.join(
            '<mergeCell ref="'+r+'"/>' for r in header_merges)+'</mergeCells>'
        changed,count=re.subn(r'<mergeCells\b[^>]*>.*?</mergeCells>',merge_xml,changed,count=1,flags=re.S)
        if count != 1:
            raise ValueError('unsupported_merge_serialization')
        changed=re.sub(r'<dimension\b[^>]*/>',f'<dimension ref="A1:Y{len(facts)+3}"/>',changed,count=1)
        ET.fromstring(changed)
        buffer=BytesIO()
        with ZipFile(buffer,'w') as output:
            for part in source.infolist():
                output.writestr(copy(part),changed.encode() if part.filename==path else source.read(part.filename))
        result=buffer.getvalue()
        projected=template_rows(result)
        if {(r['item'],r['sku']) for r in projected}!=set(facts) or any(r['state'] for r in projected):
            raise ValueError('current_sku_projection_readback_failed')
        with _archive(result) as output:
            if source.namelist()!=output.namelist() or any(
                    source.read(name)!=output.read(name) for name in source.namelist() if name!=path):
                raise ValueError('official_non_data_parts_changed')
        return result
