# 孚格 ERP 全模块对齐与数据隔离

## 这次改动

孚格不再使用只包含“产品资料与图库”的轻量页面。`deploy/fuge/compose.yml` 现在启动与畔色相同的完整 ERP 应用：产品、BOM、库存、订单、客户、定制、售后、报价、价格、财务、对账、供应商、采购、工厂单、报表、营销、NPD、导入、飞书设置和系统管理等路由与畔色保持同一套代码。

## 隔离边界

- PostgreSQL 数据目录：`${FUGE_DATA_ROOT}/postgres`，数据库名默认 `fuge_erp`。
- 文件、图库、导入产物和备份：均在 `${FUGE_DATA_ROOT}` 下的独立子目录。
- 登录令牌使用独立 `FUGE_JWT_SECRET`；不共享畔色 token、数据库、卷或图库挂载。
- API 公开返回 `instance_id=fuge`、`brand_code=FG`，便于健康检查确认没有连错实例。
- 前端构建为 `孚格 ERP`，店铺名默认 `孚格家具`；菜单和业务页面沿用完整畔色 ERP 的同一套布局和权限模型。
- `FUGE_DISABLE_SCHEDULER=1` 默认关闭订单/财务等外部自动任务；配置孚格专用账号、Agent 和群组后才能按模块启用。

## 启动与验收

1. 复制 `deploy/fuge/.env.example` 为 `deploy/fuge/.env`，设置新的数据库密码和 JWT 密钥；不要复用畔色的值。
2. 在 `deploy/fuge` 执行 `docker compose up -d --build`。
3. 访问 `http://<NAS>:8290`，确认标题为“孚格 ERP”。
4. 检查 `/api/health` 和 `/api/instance`：必须返回 `instance_id=fuge`、`brand_code=FG`，数据库必须为 `fuge_erp`。
5. 首次使用先在孚格实例内设置管理员密码，再分别导入孚格自己的产品、SKU、图库和业务数据。未导入的数据保持为空，不从畔色库复制。

## 当前边界

这是完整模块和运行隔离的工程交付；并不凭本地构建结果声称孚格淘宝/飞书真实账号已经登录或外部消息已发送。外部平台接入需要孚格自己的账号、二维码/验证码和群组配置，仍停在人工登录门。
