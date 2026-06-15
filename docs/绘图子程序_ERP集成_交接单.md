# 交接单：绘图子程序(panse-drawing) ↔ 畔色 ERP 集成

> 接收方：**Panse-System 会话**（你有生产/NAS 上下文 + 部署授权，绘图会话两次被 auto-classifier 挡在生产部署外）。
> 目标：把独立绘图子程序接到 NAS 上的畔色 ERP，让「已有产品」模式按产品编码自动拉真实尺寸做锚定。
> 日期：2026-06-15　由绘图会话(D:\AI\panse-drawing)发起。

---

## 0. 一句话结论
**真正有价值的那一半（按编码拉真实尺寸）零部署即可生效** —— 只需在绘图子程序设置里把「ERP 接口」指向 NAS。
因为尺寸来自**早已上线**的 `GET /api/products`（字段 `size_detail`），不依赖任何本期新代码。
本期新加的「BOM 下料尺寸端点」是 **dormant（0 数据）**，部署是可选、低优先。

---

## 1. 背景
- `panse-drawing` = 独立 CAD 爆炸图子程序（D 盘，FastAPI :8675，不上群晖）。
- 它通过 `erp_connector.py` 按 `product_code` 调 ERP 取尺寸做锚定。
- 调用是**服务端→服务端**（绘图后端用 Python urllib 调 ERP），**非浏览器，无 CORS 问题**。

---

## 2. 现状（均已完成并 push origin/main，HEAD 当前 e6cdfca）
| 项 | 状态 |
|---|---|
| 后端 `GET /api/products?q={code}`（既有端点，**无鉴权**） | 已在线，返回 `ProductOut` 含 `size_detail` —— **尺寸来源** |
| 后端 `GET /api/bom/{product_code}` 加物料下料尺寸字段(`material_width_mm/height_mm/area/size_type`) | 已合入 main（commit `80f5340`，是 HEAD 祖先）；**当前 0 数据，dormant** |
| 绘图侧 `erp_connector._from_api` | 已改：`size_value` → 退 `size_detail` 解析（实测 `宽度:1200mm,长度:2115mm,高度:860`→W1200/D2115/H860 正确） |
| 绘图侧 `get_bom` | 读 BOM cut sizes（端点 dormant 时返回空，不报错） |
| 部署脚本 `scripts/deploy_api_nas.sh` | 已提交（一键 build→传→up api→重启 web→验证健康） |
| 本地 api 镜像 `panse-system-api:latest` | 已 build 暂存（部署用 BUILD=1 会按当时 HEAD 重 build，无需依赖此暂存） |

---

## 3. 数据体检定论（决定了优先级，离线解析 panse.sql.gz 得出，未碰生产）
- `products.size_value`：**85/85 全「待定」** → 不可用（别用它锚定）。
- `products.size_detail`：**46/85 ≈ 54% 可解析**（格式 `宽度:1200mm，长度:2115mm，高度:860`）→ **唯一可用尺寸源**。
- `materials.width_mm/height_mm`：**0/643 全空** → BOM 下料尺寸端点 dormant，DXF 继续用几何估算。

---

## 4. 要做的事（按优先级）

### ✅ P1 — 接尺寸锚定（无需任何部署，立即有价值）
1. 确认 NAS 当前 api 的 `ProductOut` 已含 `size_detail`（应早已有；若返回里没有该字段，再走 P2 部署最新 api）。
   ```bash
   curl "http://192.168.31.21:8200/api/products?q=<某真实产品码>"
   # 期望：返回数组里有该产品，且 "size_detail":"宽度:...，长度:...，高度:..." 非空
   ```
2. 给绘图子程序配 ERP 接口（二选一）：
   - **UI**：打开绘图子程序(:8675) → 设置 → 「ERP 接口」填 `http://192.168.31.21:8200` → 保存。
   - **直接写**：`D:\AI\panse-drawing\config.json` 里 `"erp_base": "http://192.168.31.21:8200"`。
3. 验证端到端：绘图子程序「①已有产品」模式输入该产品码 → 应自动拉到 W/D/H（来自 size_detail）。
   - 或直接打绘图后端：`curl "http://localhost:8675/api/erp/<产品码>"` 看 `dims` 非 null。

> 注意：`size_detail` 只有 ~54% 产品可解析；解析不出的产品 `dims` 返回 null，绘图会优雅降级到视觉估算+人工补全，**不报错**，符合预期。

### ⬜ P2 — 部署 BOM 下料尺寸端点到 NAS（dormant，低优先，可选）
- **仅当**将来 ERP 真录入了微定制物料的下料尺寸（`materials.width_mm/height_mm`）才有意义；当前部署了也全是空。
- 命令（PC 的 **Git Bash**，不要用 PowerShell——二进制镜像流会被损坏）：
  ```bash
  cd /d/Panse-System && BUILD=1 bash scripts/deploy_api_nas.sh
  ```
- 脚本流程：build → `docker save|gzip|ssh load` → `compose -p panse-system up -d --no-build api` → **重启 web（红线：不重启则 lan nginx 缓存旧 api IP → /api 502）** → 验证 api + web `/api/health` 全绿。
- **部署即把当前 HEAD（含所有并行会话改动）一起上线** —— 部署前确认 NAS 该上的都上了、没有半成品。

---

## 5. 边界 / 红线（务必遵守）
- **件号↔物料映射未定** → DXF 下料**继续用几何估算**（已锚定真实外形）。**绝不**按名称/尺寸瞎猜映射（会给工厂错下料，违背「绝不误导工厂」红线）。
- **三方同步**：改 Panse-System 代码必须 NAS + PC + GitHub 同到一个 commit。
- `panse-drawing` **不是 git 仓库**，无需提交；它的改动只在 D 盘本地。
- 绘图子程序的 API Key 走系统加密保险库，**任何接口不回显**，别往代码/配置里写明文。

---

## 6. 关键文件索引
- 绘图侧：`D:\AI\panse-drawing\erp_connector.py`（取数）、`config_store.py`（`erp_base`）、`server.py`（`/api/erp/{code}`）
- 后端：`backend/app/api/products.py`（尺寸来源端点）、`backend/app/api/bom.py` + `backend/app/schemas/bom.py`（下料尺寸字段）
- 部署：`scripts/deploy_api_nas.sh`
- 背景文档：`docs/绘图子程序_交接方案.md`、`docs/图生爆炸图_落地方案.md`、`docs/群晖迁移_交接状态.md`

---

## 7. 验收标准
- [ ] P1：绘图子程序「已有产品」输入一个 `size_detail` 完整的产品码，能自动拉到 W/D/H 并出图。
- [ ] P1：输入一个 `size_detail` 为空/待定的产品码，`dims` 返回 null 且绘图优雅降级（不报错）。
- [ ] P2（可选）：部署后 `curl http://192.168.31.21:8200/api/health` = `{"ok":true}`，web 不 502。
