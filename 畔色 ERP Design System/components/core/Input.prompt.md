文本输入框，用于表单、筛选、搜索。聚焦时主色描边 + teal 聚焦环。可带 label、前后缀（图标/单位）、错误态。

```jsx
<Input label="客户名称" required placeholder="请输入" />
<Input prefix={<SearchOutlined/>} placeholder="搜索订单号 / 客户" />
<Input label="单价" prefix="¥" suffix="元/件" />
<Input label="邮箱" error="格式不正确" defaultValue="abc" />
```

- `size`: sm / md / lg；`prefix`/`suffix`: 图标或单位；`error`: 字符串错误信息（转红描边）
