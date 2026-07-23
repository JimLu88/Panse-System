紧凑的视图/时间段切换（如数据大盘的 今日/昨日/近7天/近30天）。2–4 个短选项时优先用它，多于此用 Select。

```jsx
<Segmented
  size="sm"
  defaultValue="30d"
  options={[
    { label: '今日', value: 'today' },
    { label: '近7天', value: '7d' },
    { label: '近30天', value: '30d' },
  ]}
  onChange={setPeriod}
/>
```
