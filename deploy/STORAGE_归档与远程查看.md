# 归档文件存哪、怎么远程看

## 一、所有原文件存哪
每次从**网页导入 / 飞书发图 / 飞书发文件 / 截图录单**进来的原始表格、图片，都会自动按
「类型 / 年 / 月」归档落盘：

```
storage/
└── imports/
    ├── orders/2026/06/{uuid}.xlsx        # 订单
    ├── alipay/2026/06/{uuid}.csv         # 支付宝流水
    ├── settlement/...                    # 微信账单 billDetail
    ├── wanshifu/  logistics/  promotion/ # 万师傅 / 物流 / 推广
    ├── refill/                           # 补单 + 代付台账
    ├── account_balance/                  # 账户余额
    ├── factory_recon/                    # 工厂对账(图/表)
    ├── purchase/                         # 采购单/进货单(图)
    ├── screenshot/                       # 没认准类型的兜底图
    └── generic/                          # 其它
```

- 磁盘上文件名是 `{uuid}.{ext}`（防重名/覆盖）；**原始文件名 + 导入结果 + 来源(web/feishu/screenshot)** 都记在数据库 `imported_files` 表里。
- 同内容文件（sha256 相同）只占一份磁盘，但每次上传都留一条记录（可追溯谁/何时/哪个入口）。

## 二、远程怎么看（3 种，推荐第 1 种）

### 1.（推荐）网页「导入档案」——任何地方浏览器都能看
- 菜单：**数据工具 → 导入档案**（路由 `/import-archive`）。
- 按类型筛选、看原始文件名/来源/上传时间/导入结果，**点「下载」拿原文件**。
- 走系统现有登录鉴权，手机/外网浏览器经你们的 **DDNS** 打开系统即可（和平时用系统一样）。
- 优点：显示的是**人能看懂的原始文件名**，不是 uuid。

### 2. 群晖 File Station 直接看挂载目录
- `docker-compose.yml` 里 api 服务已是 **bind 挂载**：`- ./storage:/app/storage`。
- 所以在群晖上，文件就在**项目目录下的 `storage/imports/类型/年/月/`**，用 **File Station / SMB 共享** 直接进去就能看/下载。
- 注意：这里看到的是 `{uuid}.xlsx` 文件名（机器名）；要对应到业务，配合上面网页「导入档案」看原始名。

### 3.（可选）映射到独立的群晖共享文件夹，方便手机/外网直接挂载
把 `docker-compose.yml`（约第 53 行）的挂载改成群晖某个共享文件夹的绝对路径，例如：

```yaml
    volumes:
      - /volume1/PanseArchive:/app/storage   # 改成你的群晖共享文件夹
```

然后 `docker compose up -d --build` 重建。之后：
- 群晖 **控制面板 → 共享文件夹** 给 `PanseArchive` 设权限；
- 手机装 **群晖 DS file** App，或电脑用 **SMB**（`\\群晖IP\PanseArchive`）/ 经 **QuickConnect / DDNS** 远程访问该文件夹。

> 三种可并存：日常对账核对用①网页档案；要整盘备份/批量拷贝用②③群晖目录。
