# 本场旧单品立减整批替换：条件计算与覆盖审计

03转达当前用户方案：先准备本次超级立减配套、截至2026-10-07 19:59:59的全部替换材料，
准备完整后才由用户暂停本窗口旧优惠、上传新总表；不动活动报名和10月88/双11。
本入口只进行本地候选计算与覆盖审计，不暂停、不上传、不生成可上传XLSX、不释放报名。

## 价格依据

- 已确证本窗口官方10%生效、P为有效基价且无剩余叠加：总单品优惠=P−official_cut(P,10%)−F。
  F=min(同一冻结ERP中促目标T,平台cap)，相对原T累计误差不超过2元，不能逐轮让价。
  使用既有取整函数（价格100元以下精确分、其余优惠向上取整至元），不用浮点直接乘0.9。
- 已确证没有活动及其他剩余优惠：总单品优惠=淘宝一口价G−T，不用ERP日常价或P替代G。
  本场无动销失败不单独证明所有历史优惠失效；需对应有效基价/优惠证据。
- 异常有P不等于活动生效，输出两种条件候选，选择状态保持未知。仅缺K可计算候选，
  仍缺实际到手价读回；缺P/cap不能验证10%分支。缺值、负值、非有限数一律不填0。
- 定制/未分类不能套普通中促目标。旧定制优惠停用后的目标、固定20%基线与具体处理决定
  必须另行确认，不能因普通款生成器跳过定制就声称它们不受影响。

## 固定入口与诚实边界

`python scripts/campaign_replacement_audit.py --input <request.json> --input-sha256 <sha256> --output <全新result.json>`

输入schema为single_discount_replacement_audit_v1，campaign为legacy/itemApply/3172207691，
target=medium、end=2026-10-07 19:59:59、当前user_authorization、platform_write=false。
rows包含精确item/sku、custom、target/big_target、list_price(G)、activity_price(P)、cap、final(K)、
effective_mode（unknown/official_10/no_official）。指定有效模式时须附effective_mode_evidence；
它由操作者对原始证据负责，程序不以任意字符串自行证明平台状态。sources逐文件path/hash校验。

old_offer_members必须是本次全部拟暂停优惠的真实完整成员，并含精确offer_id、end、item/sku；
跨窗口成员拒绝。old_offer_scope_verified默认false，不将当前活动表/首屏/拟值视为完整清单。
结果列明遗漏成员、数值候选、逐SKU错误、定制/映射/状态未知；即使候选齐全也永远
prepared_only=true、not_registered=true、upload_ready=false。此入口无正式提交守卫。
原106单品成功可在当前整批替换授权内重新覆盖，但不是重复报名超级立减，不能恢复旧prepare。

## 当前验证范围

复用02:29官方51商品454SKU、同一冻结ERP快照及本场9商品动销失败结果，只做候选审计。
映射普通329条，2条定制对应冲突另列，其余123不按普通金额强填；不是全60可售商品范围，
也不是全部旧优惠成员并集。9件尚未进入当前51商品活动列表的可售商品仍需范围补齐。
真实审计JSON在outputs/campaign-recovery-trial-20260928/replacement-audit-result.json。
未取消、改价、写ERP数据库或发出平台动作。关系流程目录未建模，维护文档明确记录缺口。
