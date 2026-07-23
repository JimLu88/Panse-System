KPI 数字卡，用于数据大盘的核心指标（收入、订单数、毛利、异常数等）。大数字用等宽字体保证对齐，权重 800。通常嵌在 `<Card>` 内。

```jsx
<Card hoverable onClick={()=>nav('/orders')}>
  <StatCard title="近 30 天收入" prefix="¥" value="1,284,560"
            icon={<DollarOutlined/>} delta="12.4%" deltaDir="up"
            footer="较上月" />
</Card>
```

- `delta` + `deltaDir`: 涨跌（up 绿 / down 红）；`valueColor`: 负值/告警时覆盖颜色（如 `var(--danger)`）
