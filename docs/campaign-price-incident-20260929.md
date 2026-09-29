# 11件59SKU撤出后价格事故接续

## 已实现与未实现

- 当前用户反馈绑定147717819883的9件50普通SKU、147928680448的2件9普通SKU；原327与两桌9原表不复用。
- 已复用固定 `discount_item_discovery` 的一个整批请求，11件/59SKU，双列表所有页；不逐条发起、不新建浏览器控制器。
- 新增精确SKU可见三列价格与表头/可见title留证、差额核查、每条缺口。未加入活动的空价格行不点“加入活动”，不加购、不保存。
- 用户重新上架床只撤销此前用户报告的下架排除；没有独立核实，也不写ERP上架状态。
- 本地review重验原job/request/file和全部分页，只输出集中缺口与冻结目标；不改成功/unknown历史。
- **尚未实现可放行的纠正表生成**：已有单品编辑面没有完整官方/其他优惠构成和门槛/叠加顺序的可靠采集契约。三列数值相减一致不证明修改后最终价仍符合目标。当前入口明确HOLD、0张XLSX，不提供假成功生成器。

要补齐最后生成能力，需03本轮只读取回真实页面材料，确认一个能提供完整精确SKU有效价构成的官方只读表面；如页面只给三列/区间/空值，就按59SKU集中报告具体缺口。不要求用户逐条截图，也不臆造页面选择器或将一口价当有效基价。

## 已准备的原请求

`D:/AI/畔色ERP系统/outputs/campaign-price-incident-20260929/read-request.json`

该请求包含59行冻结目标与原优惠ID，绝不包含重新计算的错误base/deduct。原始证据按hash锁定。

由03统一执行一次（02维护不运行capture）：

```powershell
& 'C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe' `
  'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_price_incident_review.py' capture `
  --request 'D:\AI\畔色ERP系统\outputs\campaign-price-incident-20260929\read-request.json' `
  --receipt 'D:\AI\畔色ERP系统\outputs\campaign-price-incident-20260929\read-receipt.json'
```

已存在receipt即拒绝重启调用；网络未知保留原请求，去读取原job，不另建批次。连接8502或登录不可用时停在相应门，不在此脚本启动浏览器、重启服务或自动扫码。

原job finished后消费一次：

```powershell
& 'C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe' `
  'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_price_incident_review.py' review `
  --request 'D:\AI\畔色ERP系统\outputs\campaign-price-incident-20260929\read-request.json' `
  --job-id <capture返回的原job_id> `
  --output 'D:\AI\畔色ERP系统\outputs\campaign-price-incident-20260929\price-review.json'
```

## 计划与审查

1. 只复用已存在的批量读取、精确SKU原始行、固定分页与录屏，不借旧秋季amend能力改ID。
2. 把用户口述撤出与平台读回分开；双列表没读完整不是撤出证据，旧优惠成员缺席不是全局删除。
3. 商品级区间、空字段、不上架猜测、单条截图、G/P/日常价均不能填effective_base。
4. 复合优惠、阈值/叠加未知，即使三列算式平衡仍不放行；保留真实原数据待批量核清。
5. 用户新上架覆盖旧口述下架排除，保留两条历史记录。其余8件的5件unknown/3件20SKU价格边界不改变。
6. 正反测试、安装逐文件比对、正常提交读回；不做NAS/Docker部署，不更改自动任务。

关系影响：固定平台只读 → 原始SKU价格列 → 本地差额诊断和缺口 → 冻结目标材料；不写商品/订单/财务/飞书/活动账本。关系CLI当前未映射，保留该边界，不修改他人在途目录。
