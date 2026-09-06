# 浏览器异常短恢复（仅在故障后使用）

2026-09-06 用户经主管明确要求排查、研究并修复浏览器控制。维护范围是调用用法、结果判定与可复用故障处置；不改变活动报名冻结规则，不新增每场预检。01仍是业务页唯一写入者，02只修程序/在自有公开诊断页测试，主管协调。

## 本轮已验证什么

- Edge 扩展发现、Native Host 文件存在或进程运行，都不是网页操作成功。
- 本轮公开诊断页：25秒外层超时后页面其实已创建；用新鲜清单找回同一页，60秒预算下getTab含AX读取44.498秒成功，另一轮43.850秒成功。
- 一次click+AX组合60秒超时；没有重复click，之后页面明确显示搜索框已展开。**外层超时不能证明动作失败或未执行。**
- 同一官方受控连接：domSnapshot21.669秒；独立语义click指定内部10秒，25.102秒正常返回；局部属性回读21.507秒确认关闭。内部操作超时不涵盖全部往返，不能拿它当外层总预算。
- 这些是样本，不是性能承诺。20—44秒延迟的底层原因仍未知，不能断言网络、淘宝防AI、DevTools、或Native Host故障。
- 01原秋季旧tab句柄出现“Tab not found”，不是同一类超时。官方页面读取/上传/下载分别验收，不拿诊断页成功冒充报名完成。
- 最终01对新鲜精确秋季页134751592调用getTab，外层60秒，51.6674秒返回`Timed out after 10000ms waiting for CDP command Emulation.setFocusEmulationEnabled`。这是官方连接内部命令未及时确认，不是外层调用预算问题。未取得页面正文，生产页恢复失败；不再重放该诊断，不直接调用CDP绕过，不虚构用户授权按钮。
- 后续用户正常重开Codex后，旧活动页相同内部命令仍51.846秒失败；旧公开诊断页也返回Debugger unattached。但同一Edge新受控页读取和点击成功。01复用公开`tabs.new()`返回的对象打开同一精确活动，341ms创建、21.462秒导航、21.729秒读取完整正文，旧页不动、权限不变。**新受控页恢复路径已验证，旧页附着故障根因仍未知。**

## 旧页接续失败、新受控页可用时的短恢复

此段只处理已经发生并保存证据的旧页附着故障，不是每场报名的预检，不是出错即新建页面。原页登录/验证码/权限阻挡、所有者冲突不适用；未保存编辑和结果未知的上传不能靠开新页重做。

1. 原业务任务仍是唯一写入者；先保留已知活动断点、成功批次和未决结果，原用户页面不关闭。未决写入只做只读核对，不授权重传。
2. 同一已授权Edge绑定，使用当前工具公开的`browser.tabs.new()`一次创建受控页，**复用它返回的Tab对象**。不要对该新页再用`cua.getTab`或claimTab重新接管；这两条调用路径在本次现场表现不同，不能混同。
3. 用该对象`goto`已核实的精确活动URL，另一次`playwright.domSnapshot()`验证活动身份、正文、登录/验证码及平台状态。单阶段最多60秒，不在一次调用里叠加导航和完整读取；不猜新URL、不更换浏览器/profile/控制后端。
4. 验证成功，保留返回对象继续原短流程，仅处理当前授权失败范围。需要后续继续则按公开API `markHandoff()`保留工作页；不把临时研究页标成永久交接页。
5. 新页读取仍失败或遇人工门则回报确切证据；不继续新建第二、第三页，不反复重启、不修改受管扩展。新页可读可点不等于文件传输或报名成功，仍以本次官方终态回执收口。

本轮01现场：秋季49557/49560、sign3538210379，读取到已发布7/草稿6/全部13及12%活动正文；旧维护公告仍可见，不能把它当连接错误。新页无上传的只读验收不计新报名成功，随后业务任务按主管既有授权继续。源证据在项目outputs中的`browser-post-restart-fresh-tab-control-20260906.json`及01本轮执行回执；无需重新部署ERP容器。

## 操作短卡

1. 继续使用用户指定Edge和现有会话。正常已有句柄不重复初始化、认领或打开页。工具内核重置后才按当前工具首调文档重建一次环境。
2. 排错期间把选择/认领、页面读取、动作、动作回读分成能定位的阶段。用单操作外层最多60秒的诊断预算，不在已观测44秒读取之前叠加多个慢动作。这是本次有证据的用法修正，不是无限加超时。
3. 使用当前工具已公开的网页API。整页AX慢、重复结构化操作时，可在**同一已授权Edge连接**使用公开的DOM快照、基于新鲜页面实际文本的唯一语义定位、局部状态回读。不是直接CDP、脚本注入或换后端。未公开的方法不用，不能把完整版Playwright任意方法当当前工具已具备。
4. 每个动作单独记录“开始/正常返回/实际页面效果”。超时后不重复动作；根据现有回执及一次只读回读决定从哪里继续。已成功和结果未知的上传均不重传。
5. 旧tab消失但浏览器还在：保留browser绑定，从新鲜标签列表按精确URL、标题、已有任务归属找当前页。`browser.tabs.list()`为空不能外推为全局Edge无页面；本轮该列表为空时，`cua.getState()`仍发现精确秋季页。使用公开全局清单核对后，由业务所有者按公开getTab接续；所有者冲突立即停止。禁止猜编号、跨任务force-claim或按相似标题操作。若用户是明确的tab @提及，仍按其更严格的精确匹配要求，不擅自替换。
6. 必须等待用户登录、验证码或真实授权时保留现场并立即通知。仅有超时、未附着或旧句柄错误，不能虚构需要用户点击的按钮。
7. 无新证据时不反复换页、重置、重新初始化。一次有根据的只读恢复仍失败就回报最后证据；现场存在新事实可另做相称诊断，但不能借此循环重复报名。

## 不同错误，分别处理

| 类别 | 能说明什么 | 下一步 |
|---|---|---|
| OWNER_CONFLICT | 页面由另一任务持有 | 交给原所有者，不抢占 |
| CALLER_TIMEOUT / KERNEL_RESET | 调用计时到期/执行环境重置 | 动作结果未知；一次恢复环境并只读核对，不能说浏览器拒绝 |
| CDP_COMMAND_TIMEOUT | 官方连接内部命令等待超时 | 保存准确命令/内外超时/版本；不与调用预算混同，不直接CDP绕过 |
| TAB_STALE | 标签引用过期 | 同一浏览器新鲜列表定位精确现有页，不让用户猜内部编号 |
| DEBUGGER_UNATTACHED | 页面调试连接未附着 | 保存原错误、目标和时间；仅走公开接口恢复，不猜缺权限 |
| TARGET_CLOSED | 页、上下文或浏览器生命周期终止 | 先查具体层，不重传正在下载/上传的内容 |
| NATIVE_HOST_MISSING / EXITED | 本机桥接层缺失/退出 | 只读核对注册、文件和进程；有证据才按官方安装流程修 |
| POLICY_CHECK_FAILED / TRUSTED_PACKAGE_FAILED | 权限验证或受信任包加载失败 | 明确原错误和版本；不绕过安全检查或修改托管包 |
| CLIENT_BLOCKED | 下载/网络被客户端阻止 | 给精确文件/链接，由用户处理；不默默禁用安全扩展 |
| USER_GATE | 实际页面/工具出现人工门 | 保留现场，最小操作交用户 |
| 未识别 | 尚不知是哪层 | 不给伪造原因，不报告“已修好” |

离线辅助：`python scripts/browser_control_triage.py <小型观察JSON路径>`。它只分类，不控制浏览器、不发通知、不授予重试、不保存原始错误；不是自动拦截CUA的运行代理，也不是每场报名必做门。

输入示例（不要放客户数据、token、完整页面或其他任务日志）：

```json
{
  "stage": "action",
  "error": "js execution timed out; kernel reset",
  "effect_observed": true,
  "evidence_kind": "page_readback",
  "recovery_count": 1,
  "user_gate_visible": false
}
```

该示例只说明动作效果后来看到，不说明报名成功。程序的`campaign_success`和`write_retry_authorized`始终为false；真正报名终态继续使用现有活动回执，不另建成功口径。

## 需要用户操作时怎么说

- 不再让用户寻找未经截图/当前文档核实的“连接当前页面/允许控制此页面”按钮。
- OpenAI当前官方入口是桌面应用 Settings > Computer Use，浏览器显示Manage；网站允许/阻止清单在Manage里。侧栏出现或扩展全站权限，不等于所有控制阶段成功。
- 只在证据指向此项时引导核对对应域名/正确profile。不能默认要求允许全部站点、关闭安全功能或开放full CDP。
- 如需重启浏览器/应用，先保存业务断点、说明哪些窗口和未保存内容会受影响，再由用户安排；本轮未强关任何用户浏览器或重装扩展。

## 上传/下载验收

- 下载应在触发前监听事件，等文件落地后再结束拥有下载的上下文。当前工具允许的方法以其file-uploads/下载能力文档为准；独立Playwright的saveAs不能未经暴露直接套用。
- 分清：选择了文件 → 上传返回 → 淘宝批次已生成 → 官方成功/失败终态 → 本地报告已保存。一次工具成功不跨越这些阶段。
- 新环境不以生产报名作探针，不为测上传提交空表或错误表。
- 官方文档明确内置浏览器不能自动上传本地文件，不能当作活动XLSX上传的无缝替代。
- 淘宝维护公告是独立业务不可用条件，不解释公开诊断页慢；维护窗口结束后由01读平台实际状态，不能仅凭时钟假定恢复。

## 官方依据（2026-09-06读取）

- [OpenAI Browser extension](https://learn.chatgpt.com/docs/chrome-extension)：支持Edge；安装与Manage；网站授权分离；正确profile；刷新任务/重启/官方反馈路径。上传file URL说明明确写Chrome，不直接推广为Edge故障根因。
- [OpenAI Browser](https://learn.chatgpt.com/docs/browser)：内置浏览器的文件上传限制；full CDP须单独明确授权。
- [Chrome debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger)：tab/target/session并非同一物；目标关闭或打开DevTools会触发onDetach；这只是诊断候选，不是本机已证实原因。
- [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)：扩展到本机host的标准输入输出桥、manifest与host退出排查。
- [Edge native messaging](https://learn.microsoft.com/en-us/microsoft-edge/extensions/developer-guide/native-messaging)：扩展manifest和host manifest分离，路径/allowed_origins/注册各有作用。
- [Playwright Downloads](https://playwright.dev/docs/downloads)：先监听再点击；下载完成持久化前不能结束上下文。
- [Playwright Upload files](https://playwright.dev/docs/input#upload-files)：真实file输入或filechooser；先监听，后触发，再提供文件。
- [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp)：独立扩展可接Edge/Chrome、存在文件上传工具；共享持久profile存在并发限制。只作备选研究，未安装，不承诺比现有连接快。
- 本轮CUA返回的browser-troubleshooting文档：旧tab/空列表不等于browser断开；保留现有绑定，按公开能力处理；仅明确断开才重选浏览器。

## 启用与回退

本卡通过ERP的AGENTS.md故障指引让01/02下次按同一规则处理；辅助程序仅离线分析现有证据。无需NAS部署、数据库迁移、Docker构建或重启。代码测试不代表OpenAI底层修复。若移除此修复，仅回退本卡、辅助程序及对应AGENTS索引的精确变更；不得回退活动价格/模板合同。
