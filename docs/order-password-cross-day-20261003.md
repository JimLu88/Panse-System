# 跨日口令接续

成功解密的文件名结合 ImportedFile 的原 automation_batch_id 定位完整取数证据，优先匹配原批次，不让今天另一个未完成批次挡住昨天的收口。持久 adoption 登记在最新证据被覆盖后仍可通过原 receipt SHA、source/claim 和三文件 SHA 重新核验，仅只读，不再次 apply 或导入。

正式 batch 的三份归档均须 imported、角色完整、hash 与回执一致，实际 created_at（数据库 UTC）须处于原业务日窗口至当前真实时间。允许跨日补导入，完成记录保留实际导入时间；不改 taobao_report 时间。旧日完成仅写 order_pull_completion_<batch>，不覆盖今天的完整批次与流水线。

永久密码回调沿用 complete_recovered_order_delivery → reconcile_pending_delivery、原发送账本和工厂表同步回读。没有新增按订单创建日期缩小发送范围或临时补推分支；上轮未发布补丁中的范围分支已移除。今天较新的批次存在时，较旧回调不清今天等待或写成功。

已有消息ID、发送中或 uncertain 不重发；骨架子单 SKU 码与名称均空时沿用原主单机制留自动回填队列，不占工厂号，不生成空图。no_bom/custom_size_in_remark 保持既有提示性质；本次不增加生产规格人工门，不猜 SKU 或尺寸。仅原 render_png 的 variant_size_unverified 与 production_quantity_unverified 硬门继续有效。

04负责程序验证与发布，不执行本次历史批次的发送、解密或重新导入。01已使用原程序补推的五条消息和归档保留；程序验收只读核对，不再次调用密码回调或交付入口。以后真实密码回调经同一 batch/date/manifest 自动接回原 completion。维护测试不发送真实消息；本次既有补推不作为新回调的真实端到端验收。
