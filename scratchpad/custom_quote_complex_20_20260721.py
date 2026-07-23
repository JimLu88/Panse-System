# -*- coding: utf-8 -*-
"""20 complex sideboard requests against real ERP data (read-only)."""
import json
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from app.database import SessionLocal
from app.models.product import Product
from app.services import custom_quote_v2_service as v2

BASIC, STONE, NARROW = "PPS25250090403", "PPS24250080801", "PFG25250061201"
def A(name, qty=1, **kw): return {"material": name, "qty": qty, **kw}
def M(name, old, new, qty=1): return {"name": name, "material": old, "material_real": new, "qty": qty}
def C(title, code, sku, dims, text, **kw): return dict(title=title, code=code, sku=sku, dims=dims, text=text, **kw)

CASES = [
 C("基础柜降高+双换门背板",BASIC,"PPS2525009040125",(1.5,48,170),"1.5米基础柜高改1.7米，玻璃门换樱桃木移门，洞洞板换洞石饰面柜",modify=[M("玻璃门","5mm灰色玻璃","樱桃木-2.2cm",2),M("洞洞板","亚克力洞洞板-黑色","洞石纹理饰面板")],required=["洞石纹理饰面板"]),
 C("基础柜加三抽+长虹玻璃",BASIC,"PPS2525009040120",(1.8,48,210),"1.8米基础柜高2.1米，下柜三抽，上柜换小灯芯长虹玻璃",add=[A("抽屉面板",3)],modify=[M("上柜玻璃","5mm灰色玻璃","5mm小灯芯玻璃",2)],drawers=3,required=["5mm小灯芯玻璃"]),
 C("基础柜顶柜+洞洞板+奶油白",BASIC,"PPS2525009040122",(1.2,48,230),"1.2米基础柜总高2.3米，加32厘米顶柜，背板换黑色洞洞板，整柜奶油白上色",add=[A("顶柜",height_cm=32),A("奶油白油漆上色",is_paint=True)],modify=[M("AA柱背板","实木多层板1.8cm","亚克力洞洞板-黑色")],required=["亚克力洞洞板"],paint=True),
 C("基础柜仅下柜四抽",BASIC,"PPS2525009040121",(1.5,48,90),"1.5米基础柜只保留90厘米下柜，取消上柜，下柜改四抽",add=[A("抽屉面板",4)],remove=[A("上柜")],drawers=4),
 C("基础柜黑胡桃+双层板+双抽",BASIC,"PPS2525009040120",(1.8,50,205),"1.8米基础柜改黑胡桃木，加两层板和两个抽屉，深50高205",material="黑胡桃",add=[A("层板",2),A("抽屉面板",2)],drawers=2,shelves=9,wood="黑胡桃"),
 C("洞石柜全景多抽开门三模块",STONE,"PPS2425008080133",(1.8,50,210),"1.8米洞石柜下方三列：全景柜、多抽柜、开门柜，保留洞石和轨道",add=[A("全景柜"),A("多抽柜"),A("开门柜")],drawers=4),
 C("视界柜榉木双抽实木门",STONE,"PPS2425008080138",(1.5,50,210),"1.5米视界洞石柜改榉木，视界玻璃改实木门，下柜双抽",material="榉木",add=[A("抽屉面板")],modify=[M("视界柜玻璃","5mm灰色玻璃","榉木-2.2cm")],drawers=2,wood="榉木",absent=["视界柜玻璃"]),
 C("全景柜黑胡桃双移门",STONE,"PPS2425008080132",(2.1,50,210),"2.1米全景洞石柜改黑胡桃木，玻璃移门换两扇实木移门，保留全景抽屉",material="黑胡桃",modify=[M("玻璃移门","5mm灰色玻璃","黑胡桃木-2.2cm",2)],wood="黑胡桃",absent=["玻璃移门"]),
 C("洞石背板换黑岩板+三抽",STONE,"PPS2425008080133",(1.8,52,205),"1.8米洞石柜深52高205，洞石背板换12毫米黑岩板，下柜三抽",add=[A("抽屉面板",3)],modify=[M("洞石背板","洞石纹理饰面板","12mm黑色岩板")],drawers=3,required=["12mm黑色岩板"]),
 C("洞石柜取消电轨+双层板+上色",STONE,"PPS2425008080138",(1.5,50,210),"1.5米洞石柜取消电力轨道，柜内加两层板，整柜深胡桃色上色",add=[A("层板",2),A("深胡桃色油漆上色",is_paint=True)],remove=[A("电力轨道")],shelves=9,absent=["电力轨道"],paint=True),
 C("窄柜0.6米榉木实木门",NARROW,"PFG2525006120111",(0.6,48,160),"窄柜做0.6米宽1.6米高，改榉木，顶部灰玻门改实木移门",material="榉木",modify=[M("玻璃门","5mm灰色玻璃","榉木-2.2cm",2)],wood="榉木",absent=["5mm灰色玻璃"]),
 C("窄柜樱桃木去电轨加层板",NARROW,"PFG2525006120111",(0.8,48,198),"0.8米窄柜改樱桃木，取消电力轨道，增加两层板，玻璃保留",material="樱桃木",add=[A("层板",2)],remove=[A("电力轨道")],wood="樱桃木",shelves=9,absent=["电力轨道"]),
 C("窄柜长虹玻璃+洞石台面+双抽",NARROW,"PFG2525006120112",(1.0,50,180),"1米窄柜深50高180，灰玻换小灯芯玻璃，黑岩板换洞石饰面，下柜双抽",add=[A("抽屉面板",2)],modify=[M("玻璃门","5mm灰色玻璃","5mm小灯芯玻璃",2),M("黑色岩板","12mm黑色岩板","洞石纹理饰面板")],drawers=2,required=["5mm小灯芯玻璃","洞石纹理饰面板"]),
 C("窄柜黑胡桃三抽加洞洞板",NARROW,"PFG2525006120112",(1.2,50,190),"窄柜扩到1.2米深50高190，改黑胡桃，做三抽，背部加黑色洞洞板",material="黑胡桃",add=[A("抽屉面板",3),A("亚克力洞洞板-黑色")],wood="黑胡桃",drawers=3,required=["亚克力洞洞板"]),
 C("窄柜仅下柜双抽",NARROW,"PFG2525006120111",(0.8,48,85),"0.8米窄柜只做85厘米下柜，取消上柜，改双抽，保留黑岩板",add=[A("抽屉面板",2)],remove=[A("上柜")],drawers=2),
 C("藤编门+感应灯带缺价停止",NARROW,"PFG2525006120112",(1.0,48,170),"1米窄柜玻璃门改藤编移门，再加感应灯带",modify=[M("玻璃门","5mm灰色玻璃","藤编门板",2)],add=[A("感应灯带")],missing=["藤编门板","感应灯带"]),
 C("黄铜网门缺价停止",BASIC,"PPS2525009040125",(1.5,48,190),"1.5米基础柜洞洞板区改黄铜网门，增加无线充电模块",modify=[M("洞洞板","亚克力洞洞板-黑色","黄铜网门板")],add=[A("无线充电模块")],missing=["黄铜网门板","无线充电模块"]),
 C("智能冰箱模块缺价停止",STONE,"PPS2425008080133",(1.8,55,220),"1.8米洞石柜加嵌入式智能冰箱模块和自动升降插座",add=[A("嵌入式智能冰箱模块"),A("自动升降插座")],missing=["嵌入式智能冰箱模块","自动升降插座"]),
 C("碳纤维门板缺价停止",BASIC,"PPS2525009040120",(1.8,48,210),"1.8米基础柜所有实木门改碳纤维蜂窝门板，下柜三抽",add=[A("抽屉面板",3)],modify=[M("门板","樱桃木-2.2cm","碳纤维蜂窝门板",3)],missing=["碳纤维蜂窝门板"]),
 C("洞石柜综合已知料+上色",STONE,"PPS2425008080133",(1.8,52,225),"1.8米洞石柜深52总高225，四抽一开门，灰玻换小灯芯玻璃，整柜烟熏色上色",add=[A("多抽柜"),A("开门柜"),A("烟熏色油漆上色",is_paint=True)],modify=[M("玻璃移门","5mm灰色玻璃","5mm小灯芯玻璃",2)],drawers=4,required=["5mm小灯芯玻璃"],paint=True),
]

def facts(boards):
    text=" ".join(f"{b.get('part')}|{b.get('material')}" for b in boards)
    drawers=sum(float(b.get("qty",1) or 1) for b in boards if "抽屉面板" in str(b.get("part") or ""))
    shelves=sum(float(b.get("qty",1) or 1) for b in boards if b.get("part")=="层板")
    return text,drawers,shelves

def run(db,i,c):
    product=db.query(Product).filter(Product.code==c["code"]).first()
    common=dict(base_product_code=c["code"],base_sku_code=c["sku"],category=product.category,target_length_m=c["dims"][0],target_width_cm=c["dims"][1],target_height_cm=c["dims"][2],price_tier="daily")
    got=v2.quote_both(db,**common,target_material=c.get("material"),add_parts=c.get("add",[]),remove_parts=c.get("remove",[]),modify_parts=c.get("modify",[]),description=c["text"])
    blank=v2.quote_both(db,**common,description="")
    spec,custom=got.get("spec") or {},got.get("custom") or {}
    text,drawers,shelves=facts(got.get("custom_boards") or [])
    errors=[]
    if c.get("missing"):
        miss=set(spec.get("missing_materials") or [])|set(custom.get("missing_materials") or [])
        if spec.get("final_price") is not None or custom.get("final_price") is not None: errors.append("缺价仍出价")
        for x in c["missing"]:
            if not any(x in y or y in x for y in miss): errors.append("漏报缺价:"+x)
    else:
        if spec.get("final_price") is None: errors.append("卡1停止:"+str(spec.get("error")))
        if custom.get("final_price") is None: errors.append("卡2停止:"+str(custom.get("error")))
        bp=(blank.get("custom") or {}).get("final_price")
        if custom.get("final_price") is not None and bp is not None and abs(float(custom["final_price"])-float(bp))<.01: errors.append("卡2未体现需求")
        if drawers<c.get("drawers",0): errors.append("抽屉不足")
        if shelves<c.get("shelves",0): errors.append("层板不足")
        if c.get("wood") and c["wood"] not in text: errors.append("目标木种缺失")
        for x in c.get("required",[]):
            if x not in text: errors.append("板单缺失:"+x)
        for x in c.get("absent",[]):
            if x in text: errors.append("删除失败:"+x)
        if c.get("paint") and float(custom.get("paint_surcharge") or 0)<=0: errors.append("上色未追加")
    p1,p2=spec.get("final_price"),custom.get("final_price")
    gap=round((float(p1)-float(p2))/float(p2)*100,1) if p1 and p2 else None
    return {"序号":i,"题目":c["title"],"需求":c["text"],"卡1":p1,"卡2":p2,"卡1较卡2%":gap,"抽屉":drawers,"层板":shelves,"缺价":sorted(set(spec.get("missing_materials") or [])|set(custom.get("missing_materials") or [])),"结果":"通过" if not errors else "；".join(errors)}

def main():
    db=SessionLocal()
    try: rows=[run(db,i,c) for i,c in enumerate(CASES,1)]
    finally: db.close()
    out={"count":len(rows),"passed":sum(r["结果"]=="通过" for r in rows),"rows":rows}
    Path("/tmp/custom_quote_complex_20_20260721.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False))
if __name__=="__main__": main()
