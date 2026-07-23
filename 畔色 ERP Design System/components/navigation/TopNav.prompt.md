应用顶部主导航（深青底，呼应「畔色」水岸主色）。左侧品牌 + Logo，中间横向菜单，右侧操作区（搜索、通知、头像）。

```jsx
<TopNav
  brand="畔色孚格 ERP"
  activeKey="orders"
  items={[
    { key: 'dashboard', label: '数据大盘' },
    { key: 'products', label: '产品' },
    { key: 'orders', label: '订单' },
    { key: 'finance', label: '财务' },
  ]}
  onSelect={setActive}
  right={<><Button size="sm" variant="ghost">搜索</Button><Avatar/></>}
/>
```

- 选中项为 teal 高亮（`--nav-active`）。`right` 放任意操作元素。
