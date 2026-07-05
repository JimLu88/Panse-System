# -*- coding: utf-8 -*-
"""ChatBI 问数子系统 (Plan4 v2)。

三级回答链: 模板(口径已审) → 半生成(LLM 只选指标, 代码确定性拼 SQL) → AI 直出(口径未审) → 拒答。
核心原则: 口径防错。指标口径统一定义在 metrics_dict, 只读安全走 sql_gate 六道闸。

模块:
  catalog       白名单视图 + 字段目录 (LLM 唯一可见面)
  metrics_dict  指标字典 (迷你语义层): 计算式 + 内置口径过滤 + 时间字段
  time_parser   自然语言时间解析 (上月/近30天/YYYY年M月/季度…)
  sql_gate      只读 SQL AST 安全闸门 (sqlglot: 单语句/仅SELECT/表白名单/LIMIT 注入)
  charts        查询结果 → 图表类型规则 (确定性, 不用模型)
"""
