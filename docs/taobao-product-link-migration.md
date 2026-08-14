# 淘宝商品链接追加与切主流程

适用场景：同一 ERP 产品因运营需要新建淘宝链接，旧链接保留历史销量，过渡数天后下架。

## 数据原则

- ERP 产品主体、定价、BOM、历史订单继续使用稳定的 `product_code` / `sku_code`，不复制产品和 BOM。
- `taobao_listings` 保存每一代淘宝商品 ID、skuId 与内部 SKU 的对应关系；旧记录永久保留。
- `products.taobao_id` 是当前主商品 ID，`alt_taobao_ids` 保存旧链接或过渡链接，两个 ID 都能反查同一产品。
- 活动报名只读取 `pricing_sku_promo` 当前映射。不同商品 ID 的 skuId 不能混放到 `alt_taobao_sku_ids`，否则会生成错误的“商品ID + skuId”组合。

## 两阶段操作

### 1. 追加 `add`

新链接发布后先执行追加：

1. 上传淘宝商品导出表；
2. 逐行核对新 skuId 是否唯一绑定到目标内部 SKU；
3. 把新商品 ID 加入产品备用 ID；
4. 写入新链接的 `taobao_listings`；
5. 不改活动报名主链接、不改价格、不改 BOM、不改历史订单。

接口：

- `POST /api/taobao-listings/link-migrations/preview`
- `POST /api/taobao-listings/link-migrations/apply`

表单字段：`file`、`product_code`、`mode=add`、可选 `shop`。

### 2. 切主 `activate`

旧链接准备下架时，用同一份最新导出执行 `mode=activate`：

1. 若存在未收口活动计划则拒绝切换；
2. 新商品 ID 设为产品主 ID，旧主 ID 转入备用列表；
3. 每个定价 SKU 的活动商品 ID、链接、skuId 原子切到新链接；
4. 清除旧 skuId 的平台最低价缓存，必须重新采集新 skuId 的资格证据；
5. 旧 `taobao_listings` 不删除，历史订单和销量仍能追溯。

## 必须完成的三轮校验

1. **关系校验**：新链接 SKU 数等于内部 SKU 数；新旧商品 ID 均能反查同一产品；BOM/历史订单行数不变。
2. **业务校验**：新 skuId 能解析到正确内部 SKU；财务成本仍走原定价/BOM；`add` 阶段活动映射不变，`activate` 后只生成新商品 ID + 新 skuId。
3. **生产校验**：API/Web 同一提交且健康；线上查询新旧 ID、逐 SKU 对应和活动预检结果；确认无未匹配、撞号、继承旧价格线或活动计划漂移。
