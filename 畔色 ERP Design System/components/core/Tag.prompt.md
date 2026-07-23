状态标签，用于订单状态、库存预警、对账结果等。默认软底（浅色填充 + 同色文字 + 细描边），圆角 8。

```jsx
<Tag tone="success" dot>已对账</Tag>
<Tag tone="warning">待处理</Tag>
<Tag tone="danger" dot>超卖</Tag>
<Tag tone="brand" solid>新</Tag>
```

- `tone`: default / brand / success / warning / danger / info
- `solid`: 实色填充（用于强提醒角标）；`dot`: 前置状态点；`closable`: 可关闭筛选标签
