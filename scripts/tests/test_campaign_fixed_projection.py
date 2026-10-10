from pathlib import Path
import sys
from io import BytesIO
from zipfile import ZipFile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_fixed_template_projection import project
from campaign_official_template import template_rows, read_rows, fill_selected_rows


def master():
    # Minimal actual-layout fixture. The real mother's bytes are never edited.
    def cell(col,row,value):
        return f'<c r="{col}{row}" s="1" t="inlineStr"><is><t>{value}</t></is></c>'
    headers={'A':'商品ID','D':'商品状态','E':'SKUID','J':'超级立减建议金额',
             'N':'活动价','O':'库存','P':'包邮','Q':'商品短标题',
             'W':'短视频链接 1:1','X':'让利比例','Y':'补贴金额'}
    rows=[]
    for n in range(1,6):
        values=headers if n==2 else ({'A':'1','D':'活动中','E':str(n+10),'N':'99','P':'包邮','X':'10'} if n>=4 else {})
        rows.append(f'<row r="{n}">'+''.join(cell(chr(c),n,values.get(chr(c),'')) for c in range(65,90))+'</row>')
    xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+''.join(rows)+'</sheetData><mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells></worksheet>'
    stream=BytesIO()
    with ZipFile(stream,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="商品SKU导入列表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',xml)
        z.writestr('docProps/custom.xml','<Properties><property name="1"><value>official-marker</value></property></Properties>')
        z.writestr('untouched.bin',b'must-stay-identical')
    return stream.getvalue()


def scope():
    return {'complete':True,'page_evidence':{'sha256':'a'*64},'sku_facts':[
        {'facts':{'item':'1','sku':'14','stock':'0','attributes':'现有SKU'},'sources':[{'sha256':'b'*64}]},
        {'facts':{'item':'1','sku':'99','stock':'100','attributes':'新SKU<&'},'sources':[{'sha256':'b'*64}]},
        {'facts':{'item':'2','sku':'20','stock':'100','attributes':'新商品'},'sources':[{'sha256':'b'*64}]}]}


def test_projection_replaces_stale_scope_state_and_prices_but_not_package():
    raw=master(); result=project(raw,scope(),['1','2']); rows=template_rows(result)
    assert {(r['item'],r['sku']) for r in rows}=={('1','14'),('1','99'),('2','20')}
    assert all(r['state']=='' for r in rows)
    physical=read_rows(result,'商品SKU导入列表')
    assert physical[4]['N']=='' and physical[4]['O']==''
    assert physical[4]['P']=='包邮' and physical[6]['P']==''
    assert physical[5]['F']=='新SKU<&'
    assert all(physical[n]['Y']=='' for n in (4,5,6))
    with ZipFile(BytesIO(raw)) as before,ZipFile(BytesIO(result)) as after:
        assert before.namelist()==after.namelist()
        for name in before.namelist():
            if name!='xl/worksheets/sheet1.xml':assert before.read(name)==after.read(name)


@pytest.mark.parametrize('change',['duplicate','missing','unproven','nonnumeric'])
def test_invalid_scope_not_projected(change):
    s=scope()
    if change=='duplicate':s['sku_facts'].append(s['sku_facts'][0])
    if change=='missing':s['sku_facts'].pop()
    if change=='unproven':s['sku_facts'][0]['sources']=[]
    if change=='nonnumeric':s['sku_facts'][0]['facts']['sku']='1e5'
    with pytest.raises(ValueError):project(master(),s,['1','2'])
