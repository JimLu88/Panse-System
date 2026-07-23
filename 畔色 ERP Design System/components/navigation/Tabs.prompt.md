页面内分区切换（如对账中心的 结算/诊断/工厂/代付）。下划线主色高亮，可带计数徽标。

```jsx
<Tabs
  defaultValue="all"
  items={[
    { key: 'all', label: '全部', badge: 1284 },
    { key: 'pending', label: '待处理', badge: 36 },
    { key: 'done', label: '已完成' },
  ]}
  onChange={setTab}
/>
```
