下拉选择器，用于筛选、表单选项、状态切换。自带打开/收起、点击外部关闭、选中勾。

```jsx
<Select
  placeholder="全部状态"
  defaultValue="paid"
  options={[
    { label: '待付款', value: 'pending' },
    { label: '已付款', value: 'paid' },
    { label: '已发货', value: 'shipped' },
  ]}
  onChange={(v) => setStatus(v)}
/>
```

- 受控用 `value` + `onChange`，非受控用 `defaultValue`。选项 `{label, value}`。
