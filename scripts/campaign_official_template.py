"""Preserve the official XLSX package; project only explicitly selected SKU rows.

Artifact-tool has no documented byte-preserving custom-property/package API.
This ZIP/XML adapter preserves every other part, including the platform marker.
It makes no network calls and ignores unreliable worksheet dimension metadata.
"""
from copy import copy
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import PurePosixPath
import re
from xml.sax.saxutils import escape
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
ROW = re.compile(r'<row\b[^>]*\br="(\d+)"[^>]*>.*?</row>', re.S)
CELL = re.compile(r'<c\b[^>]*?\br="([A-Z]+)(\d+)"[^>]*?(?:/>|>.*?</c>)', re.S)
REF = re.compile(r'^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$')


def money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result != result.quantize(Decimal('.01')):
            raise ValueError('invalid_money')
        return result
    except InvalidOperation as exc:
        raise ValueError('invalid_money') from exc


def _archive(raw):
    archive = ZipFile(BytesIO(raw))
    names = archive.namelist()
    if len(names) != len(set(names)) or sum(p.file_size for p in archive.infolist()) > 100_000_000:
        archive.close()
        raise ValueError('invalid_package_size_or_duplicate_parts')
    return archive


def sheet_path(archive, name):
    wb = ET.fromstring(archive.read('xl/workbook.xml'))
    candidates = [s for s in wb.findall('s:sheets/s:sheet', NS) if s.get('name') == name]
    if len(candidates) != 1:
        raise ValueError('sheet_not_unique:' + name)
    relationships = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
    links = [r for r in relationships if r.get('Id') == candidates[0].get(RID)]
    if len(links) != 1 or links[0].get('TargetMode') == 'External':
        raise ValueError('invalid_sheet_relationship')
    target = links[0].get('Target', '')
    if '..' in PurePosixPath(target).parts or '\\' in target:
        raise ValueError('invalid_sheet_target')
    return target.lstrip('/') if target.startswith('/') else 'xl/' + target


def _read(archive, path):
    xml = archive.read(path).decode('utf-8')
    root = ET.fromstring(xml)
    shared = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        strings = ET.fromstring(archive.read('xl/sharedStrings.xml'))
        shared = [''.join(t.itertext()) for t in strings]
    rows = {}
    for row in root.findall('s:sheetData/s:row', NS):
        n = int(row.get('r'))
        if n in rows:
            raise ValueError('duplicate_row')
        cells = {}
        for cell in row.findall('s:c', NS):
            match = REF.fullmatch(cell.get('r', ''))
            if not match or int(match[2]) != n or match[3] or match[1] in cells:
                raise ValueError('invalid_cell_reference')
            if cell.get('t') == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
            else:
                value = cell.findtext('s:v', default='', namespaces=NS)
                if cell.get('t') == 's':
                    value = shared[int(value)]
            cells[match[1]] = value
        rows[n] = cells
    merges = [m.get('ref') for m in root.findall('s:mergeCells/s:mergeCell', NS)]
    return xml, root, rows, merges


def read_rows(raw, name):
    """Read physical cells even when the vendor's dimension incorrectly says A1."""
    with _archive(raw) as archive:
        return _read(archive, sheet_path(archive, name))[2]


def _effective(rows, merges, n, column):
    if rows.get(n, {}).get(column, '') != '':
        return rows[n][column]
    owners = []
    for ref in merges:
        m = REF.fullmatch(ref)
        if m and m[1] == column and (m[3] or m[1]) == column and int(m[2]) <= n <= int(m[4] or m[2]):
            owners.append(rows.get(int(m[2]), {}).get(column, ''))
    if len(owners) > 1:
        raise ValueError('overlapping_merge')
    return owners[0] if owners else ''


def _layout(headers):
    common = {'A':'商品ID','D':'商品状态','E':'SKUID'}
    layouts = [
        ({'L':'官方立减默认折扣','P':'活动价','S':'官方立减报名折扣','T':'官方立减金额'}, dict(price='P',percent='S',amount='T',reference='L',last='T',numeric_percent=False)),
        ({'N':'活动价','O':'库存','P':'包邮','Q':'让利比例','R':'补贴金额','S':'商品短标题','Y':'短视频链接 1:1'}, dict(price='N',percent='Q',amount='R',reference=None,last='Y',numeric_percent=True)),
    ]
    for expected, layout in layouts:
        if all(headers.get(c) == label for c,label in {**common,**expected}.items()):
            return layout
    raise ValueError('official_template_columns_changed')


def template_rows(raw):
    with _archive(raw) as archive:
        _, _, rows, merges = _read(archive, sheet_path(archive, '商品SKU导入列表'))
        layout = _layout(rows.get(2, {}))
        return [dict(row=n, item=_effective(rows, merges, n, 'A'), sku=cells.get('E'), state=_effective(rows, merges, n, 'D'), rate=_effective(rows, merges, n, layout['reference']) if layout['reference'] else '') for n, cells in sorted(rows.items()) if n >= 4 and cells.get('E')]


def _set_cell(row_xml, column, row_number, value):
    matches = [m for m in CELL.finditer(row_xml) if m[1] == column]
    if len(matches) > 1:
        raise ValueError('duplicate_target_cell')
    if matches:
        old = matches[0]
        start = old[0].split('>', 1)[0].rstrip('/')
        start = re.sub(r'\s+t="[^"]*"', '', start)
        new = start + ('/>' if value is None else ' t="n"><v>' + value + '</v></c>')
        return row_xml[:old.start()] + new + row_xml[old.end():]
    raise ValueError('official_target_cell_missing:' + column + str(row_number))


def discount_rate(value):
    """Explicit fraction (0.10) or percent text (10%); never guess integer 10."""
    try:
        text = str(value).strip()
        rate = Decimal(text[:-1]) / 100 if text.endswith('%') else Decimal(text)
        if not rate.is_finite() or not Decimal('0') < rate < Decimal('1'):
            raise ValueError('invalid_explicit_official_rate')
        if rate * 100 != (rate * 100).quantize(Decimal('.01')):
            raise ValueError('unsupported_official_rate_precision')
        return rate
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('invalid_explicit_official_rate') from exc


def _percent_text(rate):
    return format((rate * 100).normalize(), 'f') + '%'


def _set_percent_text(row_xml, row_number, rate_text):
    matches = [m for m in CELL.finditer(row_xml) if m[1] == 'S']
    if len(matches) != 1:
        raise ValueError('official_percentage_cell_missing')
    old = matches[0]
    start = re.sub(r'\s+t="[^"]*"', '', old[0].split('>', 1)[0].rstrip('/'))
    value = start + ' t="inlineStr"><is><t>' + rate_text + '</t></is></c>'
    return row_xml[:old.start()] + value + row_xml[old.end():]


def fill_single_discount_rows(raw, selected):
    """Fill an operator-supplied current 5-column official single-discount template.

    Rows: item, sku, deduct (>0); price calculation and exact window belong to
    the same-price-version caller. No historical filters, defaults or API calls.
    Every non-data ZIP part and the original header remain byte-identical.
    """
    if not selected:
        raise ValueError('empty_discount_scope_do_not_upload_empty_file')
    seen = set()
    for row in selected:
        pair = str(row['item']), str(row['sku'])
        if not all(re.fullmatch(r'\d{8,20}', x) for x in pair) or pair in seen:
            raise ValueError('invalid_or_duplicate_discount_pair')
        if money(row['deduct']) <= 0:
            raise ValueError('nonpositive_discount')
        seen.add(pair)
    with _archive(raw) as source:
        wb = ET.fromstring(source.read('xl/workbook.xml'))
        candidates = []
        for sheet in wb.findall('s:sheets/s:sheet', NS):
            path = sheet_path(source, sheet.get('name'))
            xml, root, cells, merges = _read(source, path)
            header = cells.get(1, {})
            if header.get('A', '').lower().startswith('商品id') and header.get('B', '').startswith('SKU_ID') and header.get('C', '').startswith('优惠值'):
                candidates.append((path, xml, root, cells, merges))
        if len(candidates) != 1:
            raise ValueError('current_discount_template_columns_not_unique')
        path, xml, root, cells, merges = candidates[0]
        if merges or any(root.findall('.//s:' + tag, NS) for tag in ['f','tableParts','drawing','legacyDrawing','hyperlinks','dataValidations','conditionalFormatting','autoFilter','extLst']):
            raise ValueError('unsupported_discount_row_bound_feature')
        original = {int(m[1]): m[0] for m in ROW.finditer(xml)}
        if not {1,2}.issubset(original) or set(original) != set(cells):
            raise ValueError('discount_template_example_row_missing')
        data = [original[1]]
        for n, row in enumerate(selected, 2):
            row_xml = original[2]
            for col, value in [('A',str(row['item'])), ('B',str(row['sku']))]:
                matches = [m for m in CELL.finditer(row_xml) if m[1] == col]
                if len(matches) != 1:
                    raise ValueError('discount_identity_cell_missing')
                old = matches[0]
                start = re.sub(r'\s+t="[^"]*"','',old[0].split('>',1)[0].rstrip('/'))
                replacement = start + ' t="inlineStr"><is><t>' + escape(value) + '</t></is></c>'
                row_xml = row_xml[:old.start()] + replacement + row_xml[old.end():]
            row_xml = _set_cell(row_xml,'C',2,format(money(row['deduct']),'.2f'))
            for col in ('D','E'):
                if any(m[1] == col for m in CELL.finditer(row_xml)):
                    row_xml = _set_cell(row_xml,col,2,None)
            row_xml = re.sub(r'\br="([A-Z]*)2"', lambda m: 'r="'+m[1]+str(n)+'"',row_xml)
            data.append(row_xml)
        if xml.count('<sheetData>') != 1:
            raise ValueError('unsupported_discount_sheet_data')
        changed = re.sub(r'<sheetData>.*?</sheetData>','<sheetData>'+''.join(data)+'</sheetData>',xml,count=1,flags=re.S)
        changed = re.sub(r'<dimension\b[^>]*/>',f'<dimension ref="A1:E{len(selected)+1}"/>',changed,count=1)
        ET.fromstring(changed)
        result = BytesIO()
        with ZipFile(result,'w') as output:
            for part in source.infolist():
                output.writestr(copy(part),changed.encode() if part.filename == path else source.read(part.filename))
        with _archive(result.getvalue()) as output:
            _, _, output_rows, _ = _read(output,path)
            for n,row in enumerate(selected,2):
                if (output_rows[n]['A'],output_rows[n]['B'],money(output_rows[n]['C'])) != (str(row['item']),str(row['sku']),money(row['deduct'])):
                    raise ValueError('discount_output_readback_failed')
            if source.namelist() != output.namelist() or any(source.read(name) != output.read(name) for name in source.namelist() if name != path):
                raise ValueError('discount_non_data_package_part_changed')
        return result.getvalue()


def fill_selected_rows(raw, selected, *, official_rate):
    """Exact item/sku/activity_price and required current-event official rate.

    Does not discover campaign rules or infer them from a previous event.
    """
    rate = discount_rate(official_rate)
    rate_text = _percent_text(rate)
    if not selected:
        raise ValueError('empty_selected_scope')
    chosen = {}
    for row in selected:
        pair = str(row['item']), str(row['sku'])
        if not all(re.fullmatch(r'\d{8,20}', x) for x in pair) or pair in chosen:
            raise ValueError('invalid_or_duplicate_selected_pair')
        price = money(row['activity_price'])
        if price <= 0:
            raise ValueError('nonpositive_signup_price')
        chosen[pair] = format(price, '.2f')
    with _archive(raw) as source:
        if 'docProps/custom.xml' not in source.namelist():
            raise ValueError('official_template_marker_missing')
        properties = ET.fromstring(source.read('docProps/custom.xml'))
        if not any(p.get('name') == 'property1' and ''.join(p.itertext()).strip() for p in properties):
            raise ValueError('official_template_marker_missing')
        path = sheet_path(source, '商品SKU导入列表')
        xml, root, cells, merges = _read(source, path)
        layout = _layout(cells.get(2, {}))
        price_col, percent_col, amount_col = layout['price'], layout['percent'], layout['amount']
        if layout['numeric_percent']:
            if rate * 100 != (rate * 100).quantize(Decimal('.1')):
                raise ValueError('super_reduce_percentage_at_most_one_decimal')
            rate_text = format((rate * 100).normalize(), 'f')
        # Row-bound features need their own supported remapping, never silent loss.
        unsupported = ['f', 'tableParts', 'drawing', 'legacyDrawing', 'hyperlinks', 'dataValidations', 'conditionalFormatting', 'autoFilter', 'extLst']
        if any(root.findall('.//s:' + tag, NS) for tag in unsupported):
            raise ValueError('unsupported_row_bound_template_feature')
        identities = template_rows(raw)
        pairs = [(r['item'], r['sku']) for r in identities]
        if len(pairs) != len(set(pairs)):
            raise ValueError('duplicate_template_sku')
        if not set(chosen).issubset(set(pairs)):
            raise ValueError('selected_sku_missing_in_current_template')
        kept = [r for r in identities if (r['item'], r['sku']) in chosen]
        if any(r['state'] in ('已发布设定','活动中','进行中','已生效') for r in kept):
            raise ValueError('successful_template_scope_must_not_replay')
        # Official UI permits a rate at least its stated minimum. Blank reference
        # data is not a default: caller must still supply the current-event rate.
        if any(r['rate'] != '' and discount_rate(r['rate']) > rate for r in kept):
            raise ValueError('official_rate_below_template_minimum')
        original_rows = {int(m[1]): m[0] for m in ROW.finditer(xml)}
        if set(original_rows) != set(cells) or not {1, 2, 3}.issubset(cells):
            raise ValueError('unsupported_official_row_serialization')
        mapping = {r['row']: n for n, r in enumerate(kept, 4)}
        rewritten = {old: original_rows[old] for old in mapping}
        new_merges = []
        s_anchors = set(mapping)
        def filled_rate(value):
            return discount_rate(str(value) + '%') if layout['numeric_percent'] else discount_rate(value)
        if any((_effective(cells, merges, r['row'], percent_col) != '' and filled_rate(_effective(cells, merges, r['row'], percent_col)) != rate) or _effective(cells, merges, r['row'], amount_col) != '' for r in kept):
            raise ValueError('unexpected_prefilled_official_discount')
        for ref in merges:
            m = REF.fullmatch(ref)
            if not m:
                raise ValueError('invalid_merge_reference')
            col, begin, end_col, end = m[1], int(m[2]), m[3] or m[1], int(m[4] or m[2])
            if end <= 3:
                new_merges.append(ref)
                continue
            if begin <= 3 or col != end_col or col in ('E', price_col, amount_col):
                raise ValueError('unsupported_data_merge')
            retained = [old for old in mapping if begin <= old <= end]
            if not retained:
                continue
            # A merge must not combine different selected products.
            if len({_effective(cells, merges, old, 'A') for old in retained}) != 1:
                raise ValueError('cross_item_data_merge')
            first = retained[0]
            if first != begin:
                # Carry the original anchor's cell/style/value into the new anchor.
                anchor = next((c[0] for c in CELL.finditer(original_rows[begin]) if c[1] == col), None)
                if anchor:
                    anchor = re.sub(r'\br="[A-Z]+\d+"', f'r="{col}{first}"', anchor, count=1)
                    target = next((c for c in CELL.finditer(rewritten[first]) if c[1] == col), None)
                    if target is None:
                        raise ValueError('merged_anchor_target_cell_missing')
                    rewritten[first] = rewritten[first][:target.start()] + anchor + rewritten[first][target.end():]
            if len(retained) > 1:
                new_merges.append(f'{col}{mapping[first]}:{col}{mapping[retained[-1]]}')
            if col == percent_col:
                s_anchors.difference_update(retained[1:])
        data = [original_rows[n] for n in (1, 2, 3)]
        for row in kept:
            old, new = row['row'], mapping[row['row']]
            row_xml = _set_cell(rewritten[old], price_col, old, chosen[(row['item'], row['sku'])])
            if old in s_anchors and _effective(cells, merges, old, percent_col) == '':
                row_xml = _set_cell(row_xml,percent_col,old,rate_text) if layout['numeric_percent'] else _set_percent_text(row_xml, old, rate_text)
            row_xml = re.sub(r'\br="([A-Z]*)' + str(old) + r'"', lambda m: 'r="' + m[1] + str(new) + '"', row_xml)
            data.append(row_xml)
        if len(re.findall(r'<sheetData>', xml)) != 1:
            raise ValueError('unsupported_sheet_data_serialization')
        changed = re.sub(r'<sheetData>.*?</sheetData>', '<sheetData>' + ''.join(data) + '</sheetData>', xml, count=1, flags=re.S)
        merge_xml = '<mergeCells count="' + str(len(new_merges)) + '">' + ''.join('<mergeCell ref="' + r + '"/>' for r in new_merges) + '</mergeCells>'
        if len(re.findall(r'<mergeCells\b', changed)) != 1:
            raise ValueError('unsupported_merge_serialization')
        changed = re.sub(r'<mergeCells\b[^>]*>.*?</mergeCells>', merge_xml, changed, count=1, flags=re.S)
        changed = re.sub(r'<dimension\b[^>]*/>', f'<dimension ref="A1:{layout["last"]}{len(kept)+3}"/>', changed, count=1)
        ET.fromstring(changed)
        result = BytesIO()
        with ZipFile(result, 'w') as output:
            for part in source.infolist():
                output.writestr(copy(part), changed.encode('utf-8') if part.filename == path else source.read(part.filename))
        output_bytes = result.getvalue()
        if {(r['item'], r['sku']) for r in template_rows(output_bytes)} != set(chosen):
            raise ValueError('output_identity_readback_failed')
        values = read_rows(output_bytes, '商品SKU导入列表')
        for n, row in enumerate(kept, 4):
            if money(values[n][price_col]) != money(chosen[(row['item'], row['sku'])]):
                raise ValueError('output_price_readback_failed')
        with _archive(output_bytes) as output:
            _, _, output_cells, output_merges = _read(output, path)
            for row in kept:
                old, new = row['row'], mapping[row['row']]
                for col in (chr(c) for c in range(ord('A'),ord(layout['last'])+1) if chr(c) != price_col):
                    expected = _effective(cells, merges, old, col)
                    if col == percent_col and expected == '':
                        expected = rate_text
                    if _effective(output_cells, output_merges, new, col) != expected:
                        raise ValueError('non_price_cell_changed:' + col + str(old))
            if source.namelist() != output.namelist():
                raise ValueError('package_parts_changed')
            if any(source.read(name) != output.read(name) for name in source.namelist() if name != path):
                raise ValueError('non_data_package_part_changed')
        return output_bytes
