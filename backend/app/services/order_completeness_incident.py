"""Explicit 2026-09-20 incident recovery. No full import or factory resubmission.

Operator entry: prepare() is read-only. repair() restores only two proven missing
purchase rows and recalculates derived cost for the seven reviewed orders.
notify() sends correction copies to the configured ERP group, not new orders.
Every external message has a durable at-most-once claim; unknown is not retried.
"""
import json
from hashlib import sha256
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.models.order import Order, OrderDetail
from app.models.import_file import ImportedFile
from app.models.settings import SystemSetting
from app.services import (taobao_order_import as imp, import_storage, factory_sheet,
                          order_cost_service as costs, order_sheet_archive_service as sheets,
                          feishu_client, settings_service)

INCIDENT = 'order-completeness-20260920'
SOURCE_ID = 3027
SOURCE_HASH = '6f33ccae07416ca3cd0674c7ecf7acf2e9e628f88a6caad84d17155bfd869900'
CABINET = '5127637176073013917'
REVIEWED = {
    '3316221912811168294': (350, 1742), '3316552032920013894': (365, 1775),
    '3316795645382022882': (381, 1896), '3316976832004176569': (396, 1985),
    '5127372290988003002': (429, 2434), '3316819945093166090': (440, 2559),
    CABINET: (410, 2219),
}

# Reviewed archive fallback for old multi-product orders absent from file 3027.
FINANCE_OLD_SOURCES = {
    '4502316494125276727':1172, '4502177316184015146':485,
    '5115783241340027720':2285, '5115237121779012546':2262,
    '2701793318056034064':576, '2701788495053166684':485,
    '3305705593495183293':2833, '4991917070620599318':409,
    '5117408713503179541':2448, '3304028352501005156':2395,
    '3302834235192039473':2395, '3299694026699015351':1677,
    '5112323136333117949':1498, '3306381708756020786':2889,
    '3307673964001106764':2999,
}
FINANCE_OLD_HASHES = {
    1172:'ff246d08e09fc765c430bf70e3cf3b8ccb3a6e5acc3666747a5c51099623d9af',
    485:'8fa55cd226dc1007db30ca22c144fc819fa598a8c8aa085a4c982587323aa970',
    2285:'9ef6f201ee7655be7941c53e00ffa558b1f890b44a0f4cfbac7756331a6e7a5a',
    2262:'a119b37a8daa410a7b9987bbe8a33f4b40d1c4ec8736ee409f1fb6af688d059e',
    576:'dcbaf68d126cbb5af2b085ad4a52638bc2e620546c9ed53c9960273a0b5436d4',
    2833:'7f03ac3f5176cf5fce2faece99901057ec8d375a4c6c2fbc53188f0fb30ae7ee',
    409:'71f96082361417f8e4c42b7c47a4316c339266f4e171aba95912b167e1f58fad',
    2448:'a07f31b3cc44da58cebe9c9aab66af21586b5dee3a82823b59065f440b314e06',
    2395:'06b39bd27950d3e91048b7b7b1424412be09357efade602dddea6e9c83b15a30',
    1677:'870bdf3388fedc44299536b1b183a3282cd08e990d532786802891c6fcbfb088',
    1498:'839655b7e948c273b1b91738220f3b2fe762f5f7f10b3eef83cc597fe48400ce',
    2889:'1faffe0a676fb1794414344dcaa565cbfde9c17a3fc0e574cdbbc01443799c41',
    2999:'2abff5fbee651f38dcdecb1c3230095f7af05a7542a60360f97228b5e63667a3',
}


def prepare(db):
    source = db.get(ImportedFile, SOURCE_ID)
    if source is None or source.file_hash != SOURCE_HASH:
        raise ValueError('已核验来源文件改变，未执行')
    raw = import_storage.read(source.stored_path)
    if sha256(raw).hexdigest() != SOURCE_HASH:
        raise ValueError('原始销售明细哈希不符')
    rep = imp.TaobaoImportReport()
    parsed = imp._parse_sales_detail(source.original_filename, raw, rep)
    if rep.errors: raise ValueError('销售明细解析失败')
    result=[]
    for parent,(factory_no,file_id) in REVIEWED.items():
        order=db.scalar(select(Order).where(Order.order_no==parent))
        evidence=db.get(ImportedFile,file_id)
        if order is None or order.status not in ('shipped','signed'):
            raise ValueError(f'{parent} 状态改变，需重新核对，不重下单')
        if (not evidence or str((evidence.row_summary or {}).get('order_no'))!=parent
                or (evidence.row_summary or {}).get('pushed') is not True):
            raise ValueError(f'{parent} 原图证据不符')
        old=import_storage.read(evidence.stored_path)
        if sha256(old).hexdigest()!=evidence.file_hash:raise ValueError('旧图哈希不符')
        facts=parsed[parent].lines if parent in parsed else []
        expected=({'5127637176073040745':('PPS2455001090112',1),
                   '5127637176073059926':('PPS2455001090117',1)} if parent==CABINET
                  else {parent:('PPS2638004022511',2)})
        physical=[f for f in facts if not imp._is_service_line_name(f.get('product_name'))]
        if len(physical)!=len(expected):raise ValueError(f'{parent} 购买行数不符')
        rows=[]
        for fact in physical:
            sub=str(fact.get('sub_order_no') or '')
            if (fact.get('sku_code'),int(fact.get('qty') or 0))!=expected.get(sub):
                raise ValueError(f'{parent}/{sub} 商品或件数变化')
            row=db.scalar(select(OrderDetail).where(OrderDetail.sub_order_no==sub))
            from app.services.order_line_delivery_service import line_is_refunded
            source_status=imp._map_status(fact.get('status_text'))
            source_line=OrderDetail(line_status=source_status,refund_status=fact.get('refund_status'),refund_amount=fact.get('refund'))
            if line_is_refunded(source_line) or (row is not None and line_is_refunded(row)):
                raise ValueError(f'{sub} 有退款或关闭标记，未按正常购买重推')
            if row and (row.order_no!=parent or row.sku_code!=fact['sku_code'] or row.qty!=int(fact['qty'])):
                raise ValueError(f'{sub} 数据库与来源冲突')
            rows.append({'sub_order_no':sub,'sku_code':fact['sku_code'],'qty':int(fact['qty']),
                         'sku_name':fact.get('sku'),'missing':row is None})
        result.append({'order_no':parent,'factory_no':factory_no,'old_file_id':file_id,
                       'lines':rows,'facts':physical})
    return result


def repair(db):
    key=INCIDENT+':repair'
    prior=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    if prior:return json.loads(prior.value_plain)
    plan=prepare(db)
    changes=[]
    try:
        # The unique claim and all DB repairs commit atomically.
        claim=SystemSetting(key=key,value_plain='{}',is_secret=False,description='已授权历史订单漏项修复回执')
        db.add(claim);db.flush()
        for item in plan:
            order=db.scalar(select(Order).where(Order.order_no==item['order_no']).with_for_update())
            before={k:str(getattr(order,k)) for k in ('qty','theoretical_cost','wood_cost_est','est_parts','actual_cost','paid_amount','status')}
            if item['order_no']==CABINET:
                imp._persist_order_lines(db,order.order_no,item['facts'],imp.taobao_listing_service.build_resolver(db),enable_factory_delivery=False)
                db.flush()
                # Historical shipped rows remain excluded from new production.
                for line in db.scalars(select(OrderDetail).where(OrderDetail.order_no==CABINET,OrderDetail.source=='import')):
                    line.factory_delivery_required=False
            else:
                order.qty=2  # Same single physical SKU, exact original source proof.
            costs.recompute_and_save(db,order)
            after={k:str(getattr(order,k)) for k in before}
            changes.append({'order_no':order.order_no,'before':before,'after':after})
        financial_changes=[]
        for item in financial_plan(db):
            order=db.scalar(select(Order).where(Order.order_no==item['order_no']).with_for_update())
            if str(order.wood_cost_est)!=item['before']:raise ValueError('木作估算在核验后变化')
            from decimal import Decimal
            order.wood_cost_est=Decimal(item['after'])
            financial_changes.append(item)
        result={'status':'repaired','source_file_id':SOURCE_ID,'source_hash':SOURCE_HASH,
                'changes':changes,'financial_changes':financial_changes,
                'new_production_orders':0,'sent_messages':0}
        claim.value_plain=json.dumps(result,ensure_ascii=False)
        db.commit()
        return result
    except Exception:
        db.rollback();raise


def financial_plan(db):
    """Only the proven single-physical-SKU/unit-wood bug; no legacy guesses."""
    source=db.get(ImportedFile,SOURCE_ID)
    raw=import_storage.read(source.stored_path)
    if sha256(raw).hexdigest()!=SOURCE_HASH:raise ValueError('财务来源哈希变化')
    parsed=imp._parse_sales_detail(source.original_filename,raw,imp.TaobaoImportReport())
    changes=[]
    for order in db.scalars(select(Order).where(Order.qty>1,Order.status.in_(['paid','production','shipped','signed']))):
        if order.is_refill or order.is_custom or order.order_no not in parsed:continue
        facts=[f for f in parsed[order.order_no].lines if not imp._is_service_line_name(f.get('product_name'))]
        lines=list(db.scalars(select(OrderDetail).where(OrderDetail.order_no==order.order_no,OrderDetail.source=='import')))
        if len(facts)!=1 or len(lines)!=1:continue
        fact=facts[0];line=lines[0]
        if any(w in str(fact.get('sku') or '') for w in ('定制','咨询','补拍','差价')):continue
        if not (fact.get('sku_code')==line.sku_code==order.sku_code and
                int(fact.get('qty') or 0)==line.qty==order.qty):continue
        unit=costs._pricing_cost_for(db,order);wood=costs._pricing_wood_for(db,order)
        if wood is None or costs._effective_qty(order,unit)<=1 or order.wood_cost_est!=wood:continue
        changes.append({'order_no':order.order_no,'qty':order.qty,'before':str(order.wood_cost_est),
                        'after':str((wood*order.qty).quantize(costs._CENTS)),'source_file_id':SOURCE_ID})
    return changes


def correction_html(db,item,line):
    order=db.scalar(select(Order).where(Order.order_no==item['order_no']))
    sheet=factory_sheet.build_for_order_line(db,order.id,line.id)
    sheet.factory_no=item['factory_no']  # Display existing reference only, never assign a new DB number.
    note='历史订单更正／漏项核对，不是新增生产单。请核对实际已发件数，仅补未发部分，禁止重复生产或整单重发。'
    banner=f'<div style="width:1684px;padding:20px;background:#fff3cd;color:#b91c1c;font-size:36px;font-weight:bold">{note}</div>'
    return sheets.render_html(sheet).replace('<body>','<body>'+banner,1)


def _sales_fact_matches_pricing(db, fact, pricing):
    """Verify a historical purchase SKU without trusting a parent-SKU fallback.

    Older sales exports sometimes contain the exact item title and option but no
    internal PPS code.  In that case require an exact listing option, a single
    product identity, and an unambiguous pricing SKU.  A conflicting internal
    PPS code in the export is never overridden by today's listing table.
    """
    from app.models.pricing import PricingSku
    from app.models.taobao_listing import TaobaoListing

    if pricing is None:
        return False
    source_code = str(fact.get('sku_code') or '').strip()
    if source_code == pricing.sku_code:
        return True
    if source_code and db.scalar(select(PricingSku.id).where(PricingSku.sku_code == source_code)):
        return False
    title = str(fact.get('product_name') or '').strip()
    option = str(fact.get('sku') or '').strip()
    if not title or not option:
        return False

    def option_values(spec):
        return [part.split(':', 1)[-1].strip() for part in str(spec or '').split(';') if part.strip()]

    listings = [row for row in db.scalars(select(TaobaoListing).where(
        TaobaoListing.title == title, TaobaoListing.matched.is_(True)))
        if option in option_values(row.sku_spec)]
    if not listings:
        return False
    product_codes = {row.product_code for row in listings}
    if product_codes != {pricing.product_code}:
        return False
    listed_codes = {row.sku_code for row in listings if row.sku_code}
    if listed_codes:
        return listed_codes == {pricing.sku_code}
    candidates = set(db.scalars(select(PricingSku.sku_code).where(
        PricingSku.product_code == pricing.product_code,
        PricingSku.taobao_title == title, PricingSku.sku == option)))
    return candidates == {pricing.sku_code}


def all_product_financial_plan(db):
    """All-product derived-cost repair, only exact source/SKU/quantity matches."""
    from decimal import Decimal
    from app.models.pricing import PricingSku
    from app.services.order_line_delivery_service import line_is_refunded
    source=db.get(ImportedFile,SOURCE_ID)
    raw=import_storage.read(source.stored_path)
    if sha256(raw).hexdigest()!=SOURCE_HASH:raise ValueError('全商品财务源文件变化')
    parsed=imp._parse_sales_detail(source.original_filename,raw,imp.TaobaoImportReport())
    changes=[];unresolved=[];archive_cache={}
    for order in db.scalars(select(Order).where(Order.is_refill.is_(False),Order.is_custom.is_(False),
                                               Order.status.in_(['paid','production','shipped','signed']))):
        lines,multiple=costs._purchase_lines_for_cost(db,order)
        if not multiple or not lines:continue
        facts=parsed.get(order.order_no);source_id=SOURCE_ID
        if facts is None and order.order_no in FINANCE_OLD_SOURCES:
            source_id=FINANCE_OLD_SOURCES[order.order_no]
            if source_id not in archive_cache:
                file=db.get(ImportedFile,source_id)
                archive_raw=import_storage.read(file.stored_path)
                if sha256(archive_raw).hexdigest()!=FINANCE_OLD_HASHES[source_id]:
                    raise ValueError('旧财务来源档案哈希变化')
                archive_cache[source_id]=imp._parse_sales_detail(file.original_filename,archive_raw,imp.TaobaoImportReport())
            facts=archive_cache[source_id].get(order.order_no)
        if facts is None:
            unresolved.append({'order_no':order.order_no,'reason':'missing_source'});continue
        physical=[]
        for f in facts.lines:
            candidate=OrderDetail(line_status=imp._map_status(f.get('status_text')),
                                  refund_status=f.get('refund_status'),refund_amount=f.get('refund'))
            if not imp._is_service_line_name(f.get('product_name')) and not line_is_refunded(candidate):physical.append(f)
        expected={str(f.get('sub_order_no')):f for f in physical}
        if len(expected)!=len(physical) or set(expected)!={str(l.sub_order_no) for l in lines}:
            unresolved.append({'order_no':order.order_no,'reason':'purchase_line_set_not_exact'});continue
        verified=True
        for line in lines:
            fact=expected[str(line.sub_order_no)]
            pricing=db.scalar(select(PricingSku).where(PricingSku.sku_code==line.sku_code)) if line.sku_code else None
            if (line_is_refunded(line) or not line.sku_code
                or int(fact.get('qty') or 0)!=line.qty or pricing is None
                or pricing.physical_cost is None
                or not _sales_fact_matches_pricing(db,fact,pricing)
                or any(w in str(fact.get('sku') or '') for w in ('定制','咨询','差价','补拍'))):
                verified=False;break
        if not verified:
            unresolved.append({'order_no':order.order_no,'reason':'sku_qty_or_exact_pricing_not_verified'});continue
        pc=costs._multi_product_cost(db,order);wc=costs._multi_product_wood(db,order);ep=costs._multi_product_parts(db,order)
        if pc is None:
            unresolved.append({'order_no':order.order_no,'reason':'cost_guard_or_missing_price'});continue
        # Physical cost can be exact while a separate wood/parts split is unknown
        # (e.g. sample blocks). Correct only proven components, never invent 0.
        after={k:str(v.quantize(Decimal('0.01'))) for k,v in [('theoretical_cost',pc),('wood_cost_est',wc),('est_parts',ep)] if v is not None}
        before={k:str(getattr(order,k)) for k in after}
        if before==after:continue
        changes.append({'order_no':order.order_no,'before':before,'after':after,
                        'actual_cost':str(order.actual_cost),'paid_amount':str(order.paid_amount),'status':order.status,
                        'source_file_id':source_id,'lines':[(l.id,l.sub_order_no,l.sku_code,l.qty,
                            l.line_status,l.refund_status,str(l.refund_amount)) for l in lines]})
    return {'changes':changes,'unresolved':unresolved}


def repair_all_product_finance(db):
    """One atomic, audited correction; no actual bill or production changes."""
    from decimal import Decimal
    key=INCIDENT+':all-product-finance'
    prior=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    if prior:return json.loads(prior.value_plain)
    plan=all_product_financial_plan(db)
    claim=SystemSetting(key=key,value_plain='{}',is_secret=False,description='全品类已核原始子行派生成本修复')
    try:
        db.add(claim);db.flush()
        for item in plan['changes']:
            order=db.scalar(select(Order).where(Order.order_no==item['order_no']).with_for_update())
            for k,v in {**item['before'],'actual_cost':item['actual_cost'],'paid_amount':item['paid_amount'],'status':item['status']}.items():
                if str(getattr(order,k))!=v:raise ValueError('财务字段已并发变化')
            for lid,sub,sku,qty,status,refund_status,refund_amount in item['lines']:
                line=db.scalar(select(OrderDetail).where(OrderDetail.id==lid).with_for_update())
                if line is None or line.order_no!=order.order_no or (line.sub_order_no,line.sku_code,line.qty,
                    line.line_status,line.refund_status,str(line.refund_amount))!=(sub,sku,qty,status,refund_status,refund_amount):
                    raise ValueError('子行或退款状态已并发变化')
            for k,v in item['after'].items():setattr(order,k,Decimal(v))
        result={'status':'repaired',**plan,'new_production_orders':0,'actual_bills_changed':0}
        claim.value_plain=json.dumps(result,ensure_ascii=False);db.commit();return json.loads(claim.value_plain)
    except Exception:
        db.rollback();raise


def send_once(db,key,content_hash,sender):
    old=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    if old:
        saved=json.loads(old.value_plain)
        if saved['content_hash']!=content_hash:raise ValueError('通知内容变化，禁止覆盖旧认领')
        return {**saved,'existing':True}
    state={'status':'sending','content_hash':content_hash}
    claim=SystemSetting(key=key,value_plain=json.dumps(state),is_secret=False,description='事故更正通知防重回执')
    db.add(claim)
    try:db.commit()
    except IntegrityError:
        db.rollback();return {'status':'existing_claim','existing':True}
    try:
        receipt=sender()
        mid=receipt.get('message_id')
        if not mid:raise ValueError('missing_message_id')
        state.update(status='sent',message_id=mid)
    except Exception as exc:
        state.update(status='unknown',error_type=type(exc).__name__)
    claim.value_plain=json.dumps(state);db.commit()
    return state


def notify(db):
    if not db.scalar(select(SystemSetting).where(SystemSetting.key==INCIDENT+':repair')):
        raise ValueError('先完成修复并核验，再通知')
    chat=settings_service.get(db,'feishu_push_chat_id',env_fallback=False)
    if chat!='oc_19d0a696aca01173f99d3276ec921f5b':raise ValueError('ERP目标群已变化')
    plan=prepare(db);receipts=[]
    intro=('【历史订单数量更正／漏项核对】不是新增生产单，请勿直接整单重发。\n'
           '以下6单榉木床头柜实际购买各2件，旧图BOM误为1套：\n'+
           '\n'.join(f'畔色{x["factory_no"]}单：{x["order_no"]}' for x in plan if x['order_no']!=CABINET)+
           '\n其中3316819945093166090已由负责人确认实际漏发；其余请仓库核对实际出库件数，仅补差额。\n'
           '畔色410单：5127637176073013917，购买软木板上柜1件、带抽翻门下柜1件。两条购买明细曾漏入ERP；旧图混合上下柜信息，不能据此证明实物漏发，请逐件核对。\n'
           '后续更正图保留原工厂单号；实际已发货无须重复生产，发齐后请回复核对结果。')
    r=send_once(db,INCIDENT+':notice',sha256(intro.encode()).hexdigest(),lambda:feishu_client.send_text(db,chat,intro))
    receipts.append(r)
    if r.get('status')!='sent':return {'status':'needs_review','receipts':receipts}
    for item in plan:
        for fact in item['lines']:
            sub=fact['sub_order_no'];key=INCIDENT+':image:'+sub
            existing=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
            if existing:
                receipts.append(json.loads(existing.value_plain));continue
            line=db.scalar(select(OrderDetail).where(OrderDetail.sub_order_no==sub))
            content=sheets._html_to_png(correction_html(db,item,line),width=1684)
            # Preserve correction separately; never mark it as a new factory delivery.
            saved=import_storage.archive(db,content=content,original_name=f'{INCIDENT}-{sub}.jpg',kind='generic',source='incident_0920',
                row_summary={'order_no':item['order_no'],'sub_order_no':sub,'qty':line.qty,'old_file_id':item['old_file_id'],'not_new_production':True})
            db.commit()
            def send_image():
                image_key=feishu_client.upload_image(db,content)
                return feishu_client.send_image(db,chat,image_key)
            r=send_once(db,key,sha256(content).hexdigest(),send_image)
            receipts.append({**r,'sub_order_no':sub,'archive_id':saved.file.id})
            if r.get('status')!='sent':break
    return {'status':'sent' if all(r.get('status')=='sent' for r in receipts) else 'needs_review',
            'receipts':receipts,'chat_id':chat,'new_production_orders':0}


def lower_size_review_html(db):
    """A correction notice, explicitly NOT a production-ready dimension sheet."""
    order=db.scalar(select(Order).where(Order.order_no==CABINET))
    line=db.scalar(select(OrderDetail).where(OrderDetail.sub_order_no=='5127637176073059926'))
    if order is None or line is None or (line.order_no,line.sku_code,line.qty)!=(CABINET,'PPS2455001090117',1):
        raise ValueError('410下柜购买事实已变化')
    sheet=factory_sheet.build_for_order_line(db,order.id,line.id)
    if sheet.size_info is not None:raise ValueError('410下柜已出现新尺寸资料，需先核验，不覆盖为未知')
    sheet.factory_no=410
    sheet.image_url=None;sheet.sku_image=None;sheet.gallery_main_image=None
    sheet.size_info='专属尺寸待核对：原图中的上柜900×220×1000尺寸不适用于本下柜，禁止照做'
    banner=('<div style="width:1684px;padding:24px;color:#b91c1c;background:#fff3cd;font-size:36px;font-weight:bold">'
            '410下柜尺寸纠错：本图只核对购买数量，不得用于生产或补发。下柜专属尺寸尚未核实；原更正图尺寸栏停止使用。'
            '请核对原确认图纸，切勿重复生产或整单重发。</div>')
    return sheets.render_html(sheet).replace('工厂生产单 · PRODUCTION ORDER','历史数量核对 · 尺寸待确认').replace('<body>','<body>'+banner,1)


def notify_lower_size_review(db):
    key=INCIDENT+':410-lower-size-correction-v2'
    prior=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    if prior:return json.loads(prior.value_plain)
    chat=settings_service.get(db,'feishu_push_chat_id',env_fallback=False)
    if chat!='oc_19d0a696aca01173f99d3276ec921f5b':raise ValueError('ERP群变化')
    content=sheets._html_to_png(lower_size_review_html(db),width=1684)
    saved=import_storage.archive(db,content=content,original_name='410下柜-数量核对-尺寸待确认.jpg',
        kind='generic',source='incident_0920',row_summary={'order_no':CABINET,'sub_order_no':'5127637176073059926',
        'qty':1,'replaces_correction_archive_id':3044,'size_verified':False,'not_for_production':True,'not_new_production':True})
    db.commit()
    def sender():
        image_key=feishu_client.upload_image(db,content)
        return feishu_client.send_image(db,chat,image_key)
    return {**send_once(db,key,sha256(content).hexdigest(),sender),'archive_id':saved.file.id,
            'new_production_orders':0,'size_verified':False}
