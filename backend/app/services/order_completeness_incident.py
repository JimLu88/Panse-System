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


def all_product_financial_plan(db):
    """All-product derived-cost repair, only exact source/SKU/quantity matches."""
    from decimal import Decimal
    from app.models.pricing import PricingSku
    from app.services.order_line_delivery_service import line_is_refunded
    source=db.get(ImportedFile,SOURCE_ID)
    raw=import_storage.read(source.stored_path)
    if sha256(raw).hexdigest()!=SOURCE_HASH:raise ValueError('全商品财务源文件变化')
    parsed=imp._parse_sales_detail(source.original_filename,raw,imp.TaobaoImportReport())
    changes=[];unresolved=[]
    for order in db.scalars(select(Order).where(Order.is_refill.is_(False),Order.is_custom.is_(False),
                                               Order.status.in_(['paid','production','shipped','signed']))):
        lines,multiple=costs._purchase_lines_for_cost(db,order)
        if not multiple or not lines:continue
        facts=parsed.get(order.order_no)
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
            if (line_is_refunded(line) or fact.get('sku_code')!=line.sku_code or not line.sku_code
                or int(fact.get('qty') or 0)!=line.qty or pricing is None
                or pricing.physical_cost is None or pricing.wood_cost is None
                or any(w in str(fact.get('sku') or '') for w in ('定制','咨询','差价','补拍'))):
                verified=False;break
        if not verified:
            unresolved.append({'order_no':order.order_no,'reason':'sku_qty_or_exact_pricing_not_verified'});continue
        pc=costs._multi_product_cost(db,order);wc=costs._multi_product_wood(db,order);ep=costs._multi_product_parts(db,order)
        if pc is None or wc is None or ep is None:
            unresolved.append({'order_no':order.order_no,'reason':'cost_guard_or_missing_price'});continue
        after={k:str(v.quantize(Decimal('0.01'))) for k,v in [('theoretical_cost',pc),('wood_cost_est',wc),('est_parts',ep)]}
        before={k:str(getattr(order,k)) for k in after}
        if before==after:continue
        changes.append({'order_no':order.order_no,'before':before,'after':after,
                        'actual_cost':str(order.actual_cost),'paid_amount':str(order.paid_amount),'status':order.status,
                        'source_file_id':SOURCE_ID,'lines':[(l.id,l.sub_order_no,l.sku_code,l.qty,
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
