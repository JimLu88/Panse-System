# 活动本地统一入口与实际优惠校验

2026-09-10。本次固化已有短流程，不改变价格或授权，不执行报名、不改成功优惠，不增加任何平台预检。维护02交付，03仍是当前业务/浏览器唯一执行者；新事项不唤醒退出中的00。

## 已接入与未接入

| 入口 | 实际保护 |
| --- | --- |
| campaign_price_snapshot.py 命令 | 只读ERP一次；自动合并登记的已验证映射，不覆盖当前价格 |
| campaign_generate_current_files.py generate/命令 | 自动加载冻结合同、持久来源/历史结果；普通daily、定制原基准、完整SKU、实际优惠到手价；有问题只输出JSON、不输出可上传文件 |
| campaign_submission_gate.py claim/record 或 run_once | 再算相同文件、验证字节和范围、先持久占位后调用既有运输；未知/成功防重放，单品成功后报名 |
| build_rows、fill_selected_rows 等底层函数 | 仍为纯函数，供测试和字节保真使用；不是业务完整守卫，直接调用属于旁路 |
| 03直接 browser_file_upload/browser_click、手工网页、旧NAS活动API/自动任务、Web-Agent旧链 | 本次未拦截、未更改。不能声称这些路径已受保护或所有AI绝不会遗漏。03需在写入前消费claim，不替换其浏览器连接/权限 |

冻结合同按规范化JSON指纹固定。程序不会自动改规则或更新其指纹。该机制不是抵御有文件修改权限的恶意调用者的安全边界；本地证据必须由业务执行者根据真实回执正规化，不能凭空制作“成功”或“授权”。

## 一次生成、一次提交

仍复用本次已有快照/导出，不重新扫描。生成器新增必填 --campaign-key 精确三ID，不能从文件名猜。

```powershell
python scripts/campaign_generate_current_files.py --campaign-key 49557/49560/3538210379 --snapshot <已有ERP快照.json> --activity-template <本场最新官方模板.xlsx> --official-rate 12% --target big --start "2026-09-16 20:00:00" --end "2026-09-27 23:59:59" --output-dir <全新输出目录>
python scripts/campaign_submission_gate.py claim --bundle <receipt.json的entry_bundle_id> --phase discount
# 03只使用claim返回的精确文件/活动/窗口进行一次现有上传；不得另造文件。
python scripts/campaign_submission_gate.py record --claim <claim_id> --receipt <真实官方终态正规化回执.json>
python scripts/campaign_submission_gate.py claim --bundle <同一个entry_bundle_id> --phase signup
# 同样一次上传、一次终态记录。仅复用已成功优惠时，不需要空单品阶段。
```

原最短上传等待约3分钟/结果未知不重传不变。claim后崩溃或超时持久保留unknown，没有自动过期或解锁；需只读确认原结果并record，不可重传。两个进程不同文件同时提交同商品同场，SQLite事务只放行一个。单品优惠重叠还按窗口跨活动保护。

本地状态固定为 `D:/AI/畔色ERP系统/活动准备/报名状态/campaign-entry.sqlite3`，不位于临时会话输出中。各任务/自动任务必须使用同一权威目录默认库；自建另一库、删除库或直接操作浏览器属于未保护旁路。保护仅覆盖已登记历史和经入口提交的状态，不宣称完整平台历史已自动导入。

终态JSON格式（业务执行者填写真实值，不能照抄示例当成功）：schema=campaign_entry_terminal_v1，claim_id、campaign、phase、start、end、file_sha256、batch_id、terminal=true、items=[{item,status:success或failed}]。这些字段必须与原claim一致；缺少的商品仍unknown，不允许将终态成功改失败。`record`命令以该文件自身作为证据路径；回执应保留原官方报告/页面证据引用。测试回执不允许用于真实活动。

## 映射、首次原价与授权

`docs/campaign-entry-sources.json`及库内sources统一登记，按SHA固定读取。`campaign_entry_register.py --kind fixed|mapping|rotation|outcome|discount --receipt <已核实回执> --sha256 <已核实SHA>` 可一次补登记，之后不依赖聊天记忆/每次手传参数。已登记文件不可原地换字节；改正来源须另存新路径并明确冲突处理，不能静默替换。

- 已验证映射只按商品和ERP业务编码恢复精确SKU别名；不从名称猜测、不抄回执旧daily、不扩大商品范围。
- fixed来源须明确fixed_original_record和fixed_floor。当前价格/历史daily不能充当首次原价。首次基准沿同商品同ERP业务编码的已证实新旧SKU传递；冲突不重算20%。
- 首次不降价保持ERPdaily，缺原基准不构成扫描门；已知底线仍保护。降低只限当前精确失败SKU、固定原价已证实、确切价格授权，且>=首次原价20%。
- `--custom-corrections`是rows数组，每行item、sku、activity_price、authorization_path/authorization_sha256、failure_path/failure_sha256。授权文件精确campaign及authorized_custom_prices；失败文件精确campaign、terminal=true、batch_id、rows中的该item/sku/status=failed。没有普通SKU自动降价选项，没有旧五商品容差继承。

## 实际单品优惠，不能被理想公式覆盖

历史秋季144956016253绑定三者：保存的官方成功执行回执、精确窗口、实际上传xlsx及其SHA。逐SKU读取该文件deduct，不使用新算出来但未上传的deduct。历史成功不是2026-09-10实时读回；本次不发起刷新。

最终价计算=ERPdaily－本场既定official_cut－实际成功deduct。低于大促底价或不精确落到本场M/B均报明细，无自动容差、改价、修改优惠或重传。未知结果、缺SKU金额、多条重叠优惠、窗口不同同样列明，不能当金额0。

09-10实例：1095－132－259.52=703.48，低于704.08目标0.60。新理想优惠258.92并未上传，不能用它宣称当前到手价安全。原五商品获批容差不得推广到当前31商品。该计算是按既定舍入口径的本地判断，不是新平台拒绝/到手价实时核验。

有序闭环只保证入口覆盖及登记事实；浏览器连接、真实上传、官方成功和最终线上价须分别回验。本次仅本地代码，不修改/部署NAS旧API与定时任务。
# 2026-09-11 本场新授权补充

仅秋季 `49557/49560/3538210379` 的指定窗口/12%/大促目标，当前用户授权实际最终价正负2元以内放行（含等号）。见 `campaign-autumn-two-yuan-20260911.md`。正式生成与提交共用带来源规则；不主动减2、不放宽定制20%底线，不改变其他场次。下文严格目标规则仅被这一精确例外覆盖。
