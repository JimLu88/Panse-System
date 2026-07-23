通用内容卡片 / 面板，承载图表、表格、表单分区等。圆角 16、1px 细描边、`--shadow-xs` 柔和阴影 —— 与原系统 dashboard 卡片一致。

```jsx
<Card title="订单状态分布" extra="近 30 天">
  <Chart />
</Card>
<Card hoverable onClick={() => nav('/orders')}>…</Card>
```

- `title` / `extra`: 省略 title 则无头部；`hoverable`: 可点击卡片（上浮 + 主色描边）；`tight`: 紧凑内边距
