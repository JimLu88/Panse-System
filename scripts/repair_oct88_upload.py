"""Repair the failed October-88 whole-item workbook without changing prices.

This is a local, deterministic repair: repeat 商品ID on every physical SKU row,
make the all-inventory choice explicit, remove prior feedback columns, and run
the same manifest validator used by the generator before writing the deliverable.
"""
from copy import copy
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_official_template as tpl

ITEM = '717388593550'
SOURCE_EXPECTED = [
    ('6280250291683', '14745.00'), ('6280250291677', '15412.50'),
    ('6280250291678', '15922.50'), ('6280250291679', '16987.50'),
    ('6280250291680', '15600.00'), ('6280250291681', '16117.50'),
    ('6280250291682', '17182.50'), ('6280267963972', '14445.00'),
    ('6280267963973', '15345.00'), ('6280267963974', '16222.50'),
    ('6221831382723', '339.22'), ('6133373040412', '2000.00'),
    ('6133373040413', '2000.00'), ('6133373040414', '2000.00'),
    ('6133373040415', '2000.00'),
]
EXPECTED = [(sku, '332.22' if sku == '6221831382723' else price)
            for sku, price in SOURCE_EXPECTED]


def repair(source: Path, target: Path) -> dict:
    raw = source.read_bytes()
    rows = tpl.read_rows(raw, '商品SKU导入列表')
    actual = [(str(c.get('E', '')).strip(), str(c.get('P', '')).strip())
              for n, c in sorted(rows.items()) if n >= 4 and c.get('E')]
    if actual != SOURCE_EXPECTED:
        raise ValueError(f'current_result_scope_or_prices_changed:{actual!r}')
    with tpl._archive(raw) as archive:
        path = tpl.sheet_path(archive, '商品SKU导入列表')
        xml = archive.read(path).decode('utf-8')
        physical_rows = {int(m[1]): m[0] for m in tpl.ROW.finditer(xml)}
        data_rows = [n for n in sorted(physical_rows) if n >= 4 and rows.get(n, {}).get('E')]
        if len(data_rows) != len(EXPECTED):
            raise ValueError('unexpected_data_row_count')
        rewritten = xml
        # Replace from bottom to top so offsets remain stable.
        for n in reversed(data_rows):
            row_xml = physical_rows[n]
            row_xml = tpl._set_text_cell(row_xml, 'A', n, ITEM)
            row_xml = tpl._set_text_cell(row_xml, 'Q', n, '全部库存')
            sku = str(rows[n].get('E', '')).strip()
            new_price = dict(EXPECTED)[sku]
            row_xml = tpl._set_cell(row_xml, 'P', n, new_price)
            for col in ('U', 'V'):
                if any(m[1] == col for m in tpl.CELL.finditer(row_xml)):
                    row_xml = tpl._set_cell(row_xml, col, n, None)
            match = next(m for m in tpl.ROW.finditer(rewritten) if int(m[1]) == n)
            rewritten = rewritten[:match.start()] + row_xml + rewritten[match.end():]
        out = BytesIO()
        with ZipFile(out, 'w') as z:
            for part in archive.infolist():
                z.writestr(copy(part), rewritten.encode('utf-8') if part.filename == path else archive.read(part.filename))
        output = out.getvalue()
    expected_rows = [(ITEM, sku, price) for sku, price in EXPECTED]
    report = tpl.validate_signup_manifest(output, expected_rows)
    if not report['ok']:
        raise ValueError('repaired_manifest_invalid:' + repr(report['errors']))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output)
    return {'path': str(target), 'rows': report['rows'], 'items': report['items'],
            'sku_ids': [sku for sku, _ in EXPECTED], 'validation': report}


if __name__ == '__main__':
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    import json
    print(json.dumps(repair(source, target), ensure_ascii=False, indent=2))
