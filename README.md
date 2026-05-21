# 畔色孚格 ERP

家具电商内部 ERP — 产品/订单/库存/对账/营销 全流程 + AI 辅助 + 供应商对账 OCR。

## 在你自己电脑上跑起来

需要先装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(Windows / macOS 都有), 装完别忘了启动它。

```bash
# 1) clone (只第一次)
git clone https://github.com/JimLu88/Panse-System.git
cd Panse-System

# 2) 切到当前开发分支
git checkout claude/auto-add-custom-materials-8Wycr

# 3) 启动 (后台)
docker compose up -d

# 4) 等数据库就绪 (~30 秒), 第一次启动需要跑迁移 + 种供应商
docker compose exec api alembic upgrade head
docker compose exec api python ../scripts/seed_suppliers.py

# 5) 浏览器开:
#   前端: http://localhost:5173
#   API : http://localhost:8000/docs   (Swagger 文档)
#
# 默认账号: admin / admin (登录后立刻去 管理 → 用户管理 改密码)
```

### 配置 AI / OCR 模型

登录后进 `管理` → `AI 集成 / OCR 配置` tab, 分别为 **诊断** 和 **OCR** 配:

- Provider: `Anthropic Claude` 或 `OpenAI 兼容` (Qwen-VL / GLM-4V / 豆包 / DeepSeek / 本地 vLLM)
- API Key: 你的 key, 加密存数据库
- Base URL: 留空走官方; 中国大陆通常走代理或填 dashscope/智谱/豆包的兼容地址
- 模型: 推荐 OCR 用 `claude-opus-4-7` 或 `qwen-vl-max` (识别准); 诊断用 `claude-haiku-4-5-20251001` (便宜)

改完点 `测试联通` 验证。`已设置` 即生效, **不用重启**。

### 升级到最新代码

```bash
git pull
docker compose up -d --build
docker compose exec api alembic upgrade head
```

### 停止 / 重启 / 看日志

```bash
docker compose down                          # 停, 数据保留 (在 docker volume)
docker compose down -v                       # 停 + 删数据 (慎用!)
docker compose restart api                   # 只重启后端
docker compose logs -f api                   # 实时看后端日志
docker compose logs -f web                   # 实时看前端日志
```

### 备份数据库

```bash
docker compose exec db pg_dump -U panse panse_erp > panse-backup-$(date +%Y%m%d).sql
```

## 模块

- **产品 / 物料 / BOM**: 产品总表 + 物料单价库 + 物料分解
- **库存**: 配件库存 / 成品库存 / 自动告警
- **订单**: 平台订单 / 工厂下单 / 状态机
- **对账**: 支付宝流水智能核销 + 供应商对账 (新)
- **报价**: 轻量报价 / 高级报价 / 维度报价
- **营销 / 售后**: 推广 ROI / 售后跟进 / 退补单
- **可生产数**: 自动计算每个 SKU 能生产多少件
- **异常面板**: 数据矛盾自动捕获, 一键 AI 分析建议
- **AI 助手**: Claude / Qwen / GLM 任选, 后台可改
- **供应商对账** (本期新增):
  - 拍照上传送货单 → AI 自动 OCR → 入库 + 自动匹配订单
  - 月度对账表 (Excel + 浏览器打印 PDF)
  - 支付宝流水自动对账: 一笔流水自动匹配 1~N 张待付单据 (子集和)
  - 按月浏览原图文件夹

## 开发

```bash
# 后端 (Python 3.11+, 不用 Docker 直跑)
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000

# 前端 (Node 18+)
cd frontend
npm install
npm run dev    # http://localhost:5173, 已配代理到后端

# 测试
cd backend && python -m pytest tests/ -q     # 全部跑一遍 (276 个)
cd backend && python -m pytest tests/test_supplier_payment_matcher.py -q   # 跑某一组
```

## 文件结构

```
backend/
  app/
    api/            # FastAPI 路由
    models/         # SQLAlchemy ORM
    services/       # 业务逻辑
  alembic/          # 数据库迁移
  tests/            # pytest 测试 (276 个)
frontend/
  src/
    pages/          # 每个一级菜单一个 page
    api/client.ts   # 所有后端调用
deploy/             # nginx + HTTPS + systemd 部署模板
scripts/            # 一次性运维脚本 (种供应商 / 备份 PG)
```
