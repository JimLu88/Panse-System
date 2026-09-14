# 程序全段执行交付回执（2026-09-14）

后续更新：用户手动运行run.ps1后，02一次只读验收8500健康、continuous/activity空闲、outcomes正常events空、capabilities ready=true/missing空。下文“本机后台启动被拒绝”为历史状态，现已解除；没有新报名或真实通知发送测试。明文启动令牌打印已移除并安装，15项关联回归通过，下一次正常启动生效，不重启当前服务或Edge。详见Web-Agent程序/docs/campaign-service-readonly-acceptance-20260914.md。

## 已完成

- ERP代码05c779f88e554d3da9a63376d56cfb11af3da39b、Web-Agent代码6d21951已推GitHub main；指定运行文件已原位安装，保留无关脏改动。erp_notify的历史注释/文档字符串差异保留，新函数AST一致。
- 原有价格冻结JSON/rule_sha未改。主流程默认不再输出每job进度；原已结束job的断点由固定证据恢复器接续，不释放claim，不重传成功或未知写入。终态/通知outbox原子写入、去重，飞书专用严格确认送达；无微信降级。
- 测试：ERP活动457项、119子测试；WA新执行/终态/通知路由关联14项；后端通知与既有回归10项，全部通过。后端测试用现有Docker依赖、只读挂载源码、无网络；测试没有发送真实通知或报名。旧WA广覆盖4项夹具失败不在本次修复范围，不能说全库无失败。
- 正式deploy_release_nas.sh完整通过：API/Web同一05c779f，health/ready、关键路由/前端、DB0154head、api/web/db/backup运行通过，部署锁已释放。API启动预热期间短时502，最终恢复；回滚镜像保留在NAS。
- 实读/api/web-agent/campaign-notify-capabilities返回campaign-terminal-feishu-v1、ready=true、feishu_alert、wechat_fallback=false。此为路由就绪，不是消息实际送达回执。
- 既有真实request2c8df6bc7fb7e73bbf8425ef135806d21454c676e0cac437cf9fd5af32ada110的官方双阶段回执/录像哈希经原验收函数重新核验，acceptance SHA c8c1eda60a74ee56b0a92ea467747966b8629caa2e48a88558a1c4501a789ed5，离线能力ready=true。旧验收已保留归档；没有新报名，没有伪造新版无人值守实跑。
- 自动任务3保留ACTIVE/72小时/原03目标，已修改为开头身份确认→continuous/start一次→程序终态；禁止AI逐job轮询、分步接力或反复唤醒。erp-10原来已PAUSED，本次仍PAUSED；无新增定时，无变更订单监控。

## 尚未验收：本机后台启动被拒绝

本机Docker发布后清理：无明确已替代且可删除的容器/旧panse标签，删除容器0、镜像0；回收无用构建缓存4.958GB（可重新构建，不是业务数据）。保留API最新1b3920afe082、Web最新23c9c07bf586，其他每仓库单一版本/容器引用和基础镜像均保留；未删volume、数据库或NAS回滚镜像。引擎29.7.2正常。

8500没有监听者；唯一专用Edge8502仍是原PID38752，未关闭或重启。
本轮一次Start-Process启动固定uvicorn服务被执行工具明确拒绝：blocked by policy。未改用其他方式绕过。新版文件已安装，但主服务未启动，不能声称终态通知后台泵或下一场无人值守已经现场通过。

需要用户在本机启动既有 `D:/AI/畔色ERP系统/Web-Agent程序/run.ps1`（右键使用PowerShell运行），之后02只读验证8500健康/能力/最终结果接口；不要求03启动新报名来代替程序验收。若本机启动仍被策略阻止，保留此明确环境卡点。

## 本轮业务结果保留

秋季原request1bd5fc2953379e383bc1203c6d36c26541922eccee9561c06c68cf7d0efa97f8：57件交集，43官方确认发布、10本場动销失败、3价格例外、1升降桌历史异常。原控制器complete/pending空不等于全部报名成功。9条缺失普通优惠已补入原offer并读回，普塔856152944、云朵856154003最终均动销失败，不再重报。
轮换两件普塔/升降的新SKU在长期超级立减编辑表单活动价为空，尚无这两新SKU已有效报名的证据；本轮仅只读核对，未另行长期提交。
