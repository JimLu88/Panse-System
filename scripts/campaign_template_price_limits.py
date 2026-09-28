"""Read this official SKU template's caps, not its prefilled signup price."""
from campaign_official_template import _archive, _read, _effective, sheet_path, template_rows


def read_limits(raw):
    with _archive(raw) as archive:
        _, _, cells, merges = _read(archive, sheet_path(archive, '商品SKU导入列表'))
        if any(cells.get(2, {}).get(c) != name for c, name in {
                'H':'最低标价', 'I':'最低普惠券后价要求', 'K':'符合要求的建议价'}.items()):
            raise ValueError('official_price_limit_headers_changed')
        result = {}
        for row in template_rows(raw):
            pair = row['item'], row['sku']
            if pair in result: raise ValueError('duplicate_official_price_limit_pair')
            result[pair] = {name:_effective(cells, merges, row['row'], col)
                for col, name in [('H','price_cap'), ('I','final_cap'), ('K','suggested_price')]}
        return result
