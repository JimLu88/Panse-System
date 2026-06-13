/**
 * 字段快捷按钮条 (可重命名 / 自定义 / 增删) —— 通用, 所有表格都能用。
 *
 * 一排快捷按钮: [全部字段] [预设1] [预设2] … [⚙ 管理]。
 * 点按钮 → 只显示该预设勾选的字段; 「全部字段」= 显示所有列。
 * 「管理」里可: 改按钮名、勾选该按钮要显示哪些字段、删按钮、新增按钮。
 * 预设按 tableKey 存 localStorage(每张表各一套), 首次用内置 defaults 播种。
 */
import { useEffect, useMemo, useState } from 'react';
import { Button, Checkbox, Divider, Input, Modal, Popconfirm, Space, Typography, message } from 'antd';
import { DeleteOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons';

export interface PresetField { key: string; label: string; group?: string }
export interface Preset { id: string; name: string; fields: string[] }

function load(tableKey: string, defaults: { name: string; fields: string[] }[]): Preset[] {
  try {
    const raw = localStorage.getItem(`panse_presets_${tableKey}`);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return defaults.map((d, i) => ({ id: `b${i}_${d.name}`, name: d.name, fields: d.fields }));
}
function save(tableKey: string, presets: Preset[]) {
  localStorage.setItem(`panse_presets_${tableKey}`, JSON.stringify(presets));
}
const newId = () => `p${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;

// 从一张表的 columns 自动推出"可选字段"(label=表头, key=dataIndex/key) —— 让"全铺"只需几行。
// 标题是 JSX(如带 Tooltip)的列跳过; 无 dataIndex 的列(操作等)不进选择, 由 applyPreset 始终保留。
export function fieldsFromColumns(cols: any[]): PresetField[] {
  const out: PresetField[] = [];
  for (const c of cols || []) {
    const key = c?.dataIndex ?? c?.key;
    if (!key || typeof c.title !== 'string') continue;
    out.push({ key: String(key), label: c.title });
  }
  return out;
}

// 按选中的预设字段过滤 columns; null=全部。无 dataIndex 的列 + fixed:'right'(操作列) 始终保留。
export function applyPreset(cols: any[], visibleKeys: string[] | null): any[] {
  if (visibleKeys === null) return cols;
  const set = new Set(visibleKeys);
  return cols.filter((c) => {
    const key = c?.dataIndex ?? c?.key;
    return !key || c?.fixed === 'right' || set.has(String(key));
  });
}

export default function FieldPresetBar({
  tableKey, allFields, defaults, onChange,
}: {
  tableKey: string;
  allFields: PresetField[];
  defaults: { name: string; fields: string[] }[];
  onChange: (visibleKeys: string[] | null) => void;   // null = 全部字段
}) {
  const [presets, setPresets] = useState<Preset[]>(() => load(tableKey, defaults));
  const [activeId, setActiveId] = useState<string | null>(null);   // null = 全部字段
  const [manageOpen, setManageOpen] = useState(false);

  useEffect(() => { save(tableKey, presets); }, [tableKey, presets]);
  // 选中的预设字段变了(在管理里编辑) → 重新下发可见列
  useEffect(() => {
    if (activeId === null) { onChange(null); return; }
    const p = presets.find((x) => x.id === activeId);
    onChange(p ? p.fields : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, presets]);

  const groups = useMemo(() => {
    const m = new Map<string, PresetField[]>();
    allFields.forEach((f) => {
      const g = f.group || '其它';
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(f);
    });
    return Array.from(m.entries());
  }, [allFields]);

  const updatePreset = (id: string, patch: Partial<Preset>) =>
    setPresets((ps) => ps.map((p) => (p.id === id ? { ...p, ...patch } : p)));
  const addPreset = () =>
    setPresets((ps) => [...ps, { id: newId(), name: `新按钮${ps.length + 1}`, fields: [] }]);
  const delPreset = (id: string) => {
    setPresets((ps) => ps.filter((p) => p.id !== id));
    if (activeId === id) setActiveId(null);
  };

  return (
    <>
      <Space size={4} wrap>
        <Button size="small" type={activeId === null ? 'primary' : 'default'} onClick={() => setActiveId(null)}>全部字段</Button>
        {presets.map((p) => (
          <Button key={p.id} size="small" type={activeId === p.id ? 'primary' : 'default'} onClick={() => setActiveId(p.id)}>
            {p.name}
          </Button>
        ))}
        <Button size="small" icon={<SettingOutlined />} onClick={() => setManageOpen(true)}>管理按钮</Button>
      </Space>

      <Modal title="管理快捷按钮" open={manageOpen} onCancel={() => setManageOpen(false)} footer={null} width={620} destroyOnClose>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          每个按钮 = 一组要显示的字段。点按钮名可改名；勾选下面的字段决定这个按钮显示哪些列；可新增/删除按钮。改完即时生效、自动保存。
        </Typography.Paragraph>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {presets.map((p) => (
            <div key={p.id} style={{ border: '1px solid #eee', borderRadius: 8, padding: 10 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Input
                  value={p.name} style={{ width: 220, fontWeight: 600 }} size="small"
                  onChange={(e) => updatePreset(p.id, { name: e.target.value })}
                />
                <Space>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>已选 {p.fields.length} 字段</Typography.Text>
                  <Popconfirm title={`删除按钮「${p.name}」？`} okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => delPreset(p.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              </Space>
              <Divider style={{ margin: '8px 0' }} />
              <div style={{ maxHeight: 220, overflow: 'auto' }}>
                {groups.map(([g, fs]) => (
                  <div key={g} style={{ marginBottom: 8 }}>
                    <Typography.Text strong style={{ fontSize: 12 }}>{g}</Typography.Text>
                    <div style={{ marginTop: 2 }}>
                      <Checkbox.Group
                        options={fs.map((f) => ({ label: f.label, value: f.key }))}
                        value={p.fields}
                        onChange={(v) => updatePreset(p.id, { fields: v as string[] })}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <Button icon={<PlusOutlined />} onClick={() => { addPreset(); message.success('已新增按钮，给它改名并勾选字段'); }}>新增快捷按钮</Button>
        </Space>
      </Modal>
    </>
  );
}
