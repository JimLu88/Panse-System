主操作按钮，用于表单提交、工具栏动作、弹窗确认等。primary 用于页面主操作（每屏 1 个），secondary 用于次要操作，ghost/text 用于轻量动作。

```jsx
<Button variant="primary" onClick={save}>保存</Button>
<Button variant="secondary" icon={<PlusOutlined />}>新增</Button>
<Button variant="text" danger>删除</Button>
<Button variant="primary" loading block>提交中…</Button>
```

- `variant`: primary / secondary / ghost / text
- `size`: sm(28) / md(36) / lg(44) — 高密度表格行内用 sm
- `danger`: 危险操作转红；`loading`: 转圈并禁用；`block`: 占满宽度
