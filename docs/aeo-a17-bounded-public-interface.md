# AEO-A17 限定公开接口本地交付

续接任务 AEO-A17-BOUNDED-PUBLIC-INTERFACE-LOCAL-20261009；本轮仅本地代码与内存SQLite fixture，没有采集ERP、全量目录或部署。AEO消费者与真实一次采集的唯一执行者为01a072b0-e400-7ee2-8e7f-42055b9e489b。

## 实际CLI合同

生产者脚本 `backend/scripts/export_aeo_bounded_snapshot.py`，依赖已有SQLAlchemy和ERP的app.database。导入模块、--help、范围验证不连接数据库；仅合法范围通过后main才导入app.database。

在已批准的ERP执行环境由现有受控只读桥执行（以下是合同，ERP维护本轮未执行）：

```text
python scripts/export_aeo_bounded_snapshot.py --scope-json <明确范围.json> --stdout
python scripts/export_aeo_bounded_snapshot.py --scope-inline <同一范围JSON字符串> --stdout
```

`--out <新文件>`与`--stdout`二选一；目标文件存在则拒绝覆盖。stdout成功仅JSON，失败exit2且stderr只打印异常类型，不泄漏SQL/凭据。--scope-json与--scope-inline二选一且必填。桥若通过stdin发送Python源码，必须设置`__file__='<...>'`并以ERP backend为cwd，argv仍按上述合同；不要拼接未经转义的shell字符串。不要调用旧export_aeo_public_snapshot.py或旧全量Product出口。

精确范围fixture为docs/fixtures/aeo-a17-scope.json，从原A17批准候选逐项转写：4产品、5SKU，包括榉木窄规格PPS2638004022512。acceptedProductSkuMappings加narrowAdditionalReadScope必须恰好覆盖请求集合，拒绝缺失、空、重复、错配、未知商品或SKU、定制占位。范围本身不是新的授权；调用者须校验当次授权集合等于该fixture，不能扩展。

## 返回与消费者适配

- schemaVersion=1，mode=read-only-public-product-snapshot，新增contractVersion=erp.aeo-bounded-public.v1；旧消费者顶层白名单需显式接纳新合同，不移除或放宽隐私校验。
- products只有指定产品的公开身份、材质、尺寸说明、图片候选、上架状态及源更新时间；不读取Product对象全列、不输出主表可能指向其他规格的skuCode。
- pricing只有指定SKU：productCode、skuCode、sku、sizeInfo、imageUrl、updatedAt、midBuyerPrice（两位小数字符串或null）、currency=CNY、priceBasis=中促到手价、sourceField=PricingSkuPromo.mid_buyer_price、sourceUpdatedAt、observedAt。没有dailyPrice，不重算、不写价；null是原值未知，不能当0或用日常价补。
- sourceUpdatedAt是promo行更新时间，不冒充独立价格变更时间；缺行/缺源时间拒绝。generatedAt/observedAt仅真实执行当时产生，fixture不是新鲜业务快照。
- dimensions=[]明确不读产品级尺寸资产JSON，因为无法保证其中skuVariants不含未授权SKU。公开尺寸仍在指定产品sizeDetail/sizeValue和SKU sizeInfo里；不得据此把未确认高度变成已确认。图片仍只是候选，不代表许可或精确SKU绑定。
- scope包含精确productSkuMappings、requestedProductCodes、requestedSkuCodes、scopeSha256和coverage=selected-product-sku-only；counts必须等于实际数组。可调用同文件validate_snapshot(snapshot,scope)纯本地严格复核，但不能把验证当原始数据真实性签名。

## 只读与查询边界

PostgreSQL使用REPEATABLE READ、READ ONLY及10秒statement_timeout；SQLite使用query_only。两条业务SELECT均先按明确集合过滤，显式列投影，只涉及products、pricing_sku、pricing_sku_promo；不访问订单、客户、成本、供应商或工厂表，也不选择同表成本字段。事务结束rollback。

关系CLI在权威目录对此新scripts入口报告unmapped；已审语义只传播到AEO公开快照，不改变订单、价格、库存、通知。隔离Git基线缺app.cli.business_relationships模块，未伪称其运行成功，也未重锁其他源码。此接口未登记为HTTP路由、不授予任何匿名读权限。

## 复验

```text
python backend/tests/test_aeo_bounded_snapshot.py -v
python backend/scripts/export_aeo_bounded_snapshot.py --help
python -m py_compile backend/scripts/export_aeo_bounded_snapshot.py
```

测试只创建SQLite内存表，未导入app.database；测试通过不表示当前生产字段/数据已经实读成功。AEO维护先完成新合同适配，校验脚本与scope指纹后独占执行已授权的一次采集；不并发采集，不自动回落旧出口。ERP程序维护不代执行本次采集、不修改AEO文件。
