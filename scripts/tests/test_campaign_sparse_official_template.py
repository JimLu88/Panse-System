from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import importlib.util
import re
import pytest

spec = importlib.util.spec_from_file_location('sparse_template_candidate', Path(__file__).parents[1]/'campaign_official_template.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def package():
    headers = dict(A='商品ID', D='商品状态', E='SKUID', L='官方立减默认折扣', P='活动价', S='官方立减报名折扣', T='官方立减金额')
    def row(n, values):
        return f'<row r="{n}">'+''.join(f'<c r="{k}{n}" t="inlineStr"><is><t>{v}</t></is></c>' for k,v in values.items())+'</row>'
    xml = '<worksheet xmlns="'+m.NS['s']+'"><sheetData>'+row(1,{'A':'标题'})+row(2,headers)+row(3,{'A':'说明'})+row(4,dict(A='12345678901',D='待报名',E='23456789012',L='15%',P='',S='',T=''))+row(5,dict(E='23456789013',P='',T=''))+'</sheetData><mergeCells>'+''.join(f'<mergeCell ref="{c}4:{c}5"/>' for c in ('A','D','L','S'))+'</mergeCells></worksheet>'
    out=BytesIO()
    with ZipFile(out,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="'+m.NS['s']+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="商品SKU导入列表" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('docProps/custom.xml','<Properties><property name="property1">original-marker</property></Properties>')
        z.writestr('xl/worksheets/sheet1.xml',xml)
    return out.getvalue()

@pytest.mark.parametrize('subset',[False,True])
def test_sparse_merged_cells_and_removed_anchor(subset):
    raw=package()
    rows=m.template_rows(raw)[int(subset):]
    selected=[dict(item=r['item'],sku=r['sku'],activity_price='123.45') for r in rows]
    result=m.fill_selected_rows(raw,selected,official_rate='15%')
    assert m.validate_signup_manifest(result)['ok']
    with ZipFile(BytesIO(raw)) as a, ZipFile(BytesIO(result)) as b:
        for name in a.namelist():
            if name!='xl/worksheets/sheet1.xml':assert a.read(name)==b.read(name)

def test_unmerged_missing_target_still_rejected():
    with pytest.raises(ValueError,match='official_text_cell_missing'):
        m._set_text_cell('<row r="5"><c r="E5"/></row>','A',5,'12345678901')
