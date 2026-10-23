# 原SKU外尺寸与板单报价修复

适用：已确认原SKU、床体外尺寸、大促到手价的定制报价。仅计算，不改商品、材料、活动。

## 普通定制

POST `/api/customization/v2/quote-light`，保留 base_product_code、base_sku_code、price_tier=big_buyer、remove_parts。
用 `outer_dimensions: {length_cm:200,width_cm:170}` 替代旧 target_length_m/target_width_cm/target_height_cm，长指床纵向，宽指横向。
原款尺寸只读 SKU.size_info，不再从“1.8米”床垫规格解析。锁定准确SKU到手价。
沿用面积估算，但仅木作成本按面积比调整；配件、打包、物流、安装不缩放，成本差按原SKU到手价/物理成本转为报价差；不再对已确定到手价重复加通用安全系数。
增减部件沿用既有材料删减规则（style_remove_credit），不宣称是工厂实际节省，不擅改全局参数。
可显式提供 reference_length_cm/reference_width_cm/reference_source；必须成对和有来源。输出同时保留ERP尺寸及冲突，不写回ERP。
原款当前ERP外长210cm、宽185cm；用户图说明208cm，应保留冲突。两者缩至200×170均为缩小。
现有quote-both未验收外尺寸自动板单，拒绝此新字段，改用两条独立入口，不静默忽略。

## 全定制

POST `/api/customization/board-quote` 可新增同一 base_product_code/base_sku_code/price_tier=big_buyer。
打包/物流/安装严格读原SKU；缺失不回退通用类目。物料库优先，配置备用，返回实际单价和两个来源，不修改240/280的差异。
`big_buyer_price = 板单物理成本 × 原SKU.big_buyer_price / 原SKU.physical_cost`。
保留工厂人工/利润的成本计算，但不再叠加通用零售利润、安全系数或平台折扣。未绑定SKU的旧通用报价不变，且不会标成big_buyer_price。
输出 estimate_only=true；只有用户传入的板件被计算。支撑、脚未确认，铺板650是原整套预算，不是假造完整板单或最终核准报价。

## 独立验收

`python -m app.cli.custom_quote_verify` 从stdin收 {light:{案例名:参数},board:参数}。
实时读取原SKU，返回输入输出及全表PricingSku/PricingSkuPromo/PricingSkuCosts/Material前后指纹，finally rollback，无报价日志提交。
95项原有及新增测试通过；仍须正式发布和生产只读读回，不能以此宣称上线。
不改变中促>=大促1.03、普通SKU定价、材料价格、订单或活动。没有执行报名、重新计算商品定价或全局配置写入。
