# 本地整批制表：按商品隔离 + 当前官方卡控

仅生成本地文件与既有账本bundle；不上传、不报名、不撤出、不重启，不另建业务状态库。

新参数均显式启用，旧批次再验证保持旧算法：

- `--isolate-item-issues`：生成、定制修正、优惠复用的明确商品级问题，仅隔离该商品的完整报名及单品行；其他商品同轮两表。全局来源哈希、身份、公式或无法归属商品的错误仍报错。receipt保留全部异常和隔离/覆盖统计。
- `--official-price-limits`：只读取本次官方模板H最低标价、I最低普惠券后价要求、K符合要求的建议价，验证表头与合并格。普通报名价仍ERP daily，最终价在同一原目标累计2元内匹配cap；不降低daily。定制目标null不属缺价；需要降低时只有官方建议价与确定固定20%底线允许，缺建议/基线或低于底线即隔离。G标价/P旧报名价不替代ERP价。该参数仅用于具备所需表头的官方模板，不适用于超级立减导出。
- `--product-export-observation`：引用完整多页product_export已完成回执，复核店铺、请求、全部导出文件hash、页数与商品数；本次解析全部文件，不把单页注册为全店latest。不修改全局SKU索引。精确同商品唯一ERP编码只补备选绑定，不改价格和主绑定；既有B1需原验证别名，普通相近名称不猜配。

生成bundle保存这些输入，验证器从相同原件重算，隔离商品不进入新bundle；未知/成功历史及实际旧优惠检查不清空。交付后依然由用户上传；不能因exit 0/3称作报名成功。

退出码：0无局部异常；3其他完整商品文件已生成但仍有隔离；2全部阻断/无交付。异常exit 1等不自动重试。新输出目录不可已存在，不覆盖旧表。

本次双11参数（秒级窗口来自03转交的用户明确确认，不冒充官方API回执）：

```powershell
& 'C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe' 'D:\AI\畔色ERP系统\ERP程序\scripts\campaign_generate_current_files.py' --campaign-key '49646/49651/3555037899' --snapshot 'D:\AI\畔色ERP系统\outputs\01a067c6-7e83-7483-9a21-84b44ed7299b\consolidated-20260929\erp-snapshot.json' --activity-template 'C:\Users\lzdwy\Desktop\「2026年淘宝双11全球狂欢季双11现货」商品导入模版20260928234533.xlsx' --official-rate '15%' --target big --start '2026-10-20 20:00:00' --end '2026-11-13 23:59:59' --isolate-item-issues --official-price-limits --product-export-observation 'D:\AI\畔色ERP系统\outputs\01a067c6-7e83-7483-9a21-84b44ed7299b\rotation-20260928\export-latest.json' --output-dir 'D:\AI\畔色ERP系统\outputs\01a067c6-7e83-7483-9a21-84b44ed7299b\consolidated-20260929\double11-local-batch'
```

维护只交付入口，由03执行一次业务出表。9条确认轮换映射的注册回执：`D:/AI/畔色ERP系统/outputs/campaign-consolidated-handoff-20260928/verified-nine-mapping.json`，已在原campaign-entry.sqlite3登记mapping；不修改ERP价格/主SKU。

超级四条P/final/cap空值不是0或标价，也不是新报名权限。仍走既有同营销失败范围恢复与成功/未知保护；本补丁不解除该保护、不生成重复整商品恢复表，不再次索取同一导出。

关系影响：本机官方模板/最新多页导出→精确映射与ERP冻结价→商品隔离/两表→原bundle及复核。订单、财务、库存、飞书和NAS无写入；关系CLI这些本机脚本仍未建模，不能把未映射说成无影响，不更改其他人在途catalog或锁指纹。
