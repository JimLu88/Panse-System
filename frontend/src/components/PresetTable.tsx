/**
 * 通用「带字段管理按钮」的表格 —— 在普通 AntD Table 头部挂一排
 *   [全部字段] [预设1] [预设2] … [管理按钮]
 * 用法: 把 <Table ...> 换成 <PresetTable tableKey="alipay" ...>, 其余 props 原样透传。
 * 列的显示/隐藏按 tableKey 存 localStorage(每张表各一套, key=panse_presets_<tableKey>)。
 *
 * 这是「管理按钮全铺所有页面」的统一载体: 每个列表页只需 import + 换标签 + 传 tableKey,
 * 不必再各自重复 useState/FieldPresetBar/applyPreset 的样板。
 */
import { useState, type ReactNode } from 'react';
import { Button, Table, message } from 'antd';
import { FileExcelOutlined } from '@ant-design/icons';
import type { TableProps } from 'antd';
import FieldPresetBar, { fieldsFromColumns, applyPreset } from './FieldPresetBar';

// 导出当页为 Excel (用户需求 2026-06-11: 每个页面都要有, 导出记录进 工具→导入档案→页面导出)
async function exportToExcel(tableKey: string, cols: any[], rows: readonly any[]) {
  const exportCols = cols
    .filter((c) => c && (c.dataIndex != null))
    .map((c) => ({ key: String(c.dataIndex), title: typeof c.title === 'string' ? c.title : String(c.dataIndex) }));
  if (!exportCols.length) { message.warning('该表没有可导出的数据列'); return; }
  const plainRows = (rows ?? []).map((r) => {
    const o: Record<string, unknown> = {};
    for (const c of exportCols) {
      const v = (r as any)[c.key];
      o[c.key] = v == null || typeof v === 'object' ? (v == null ? null : JSON.stringify(v)) : v;
    }
    return o;
  });
  try {
    const { api } = await import('../api/client');
    const resp = await api.post('/api/exports/page',
      { title: tableKey, columns: exportCols, rows: plainRows },
      { responseType: 'blob' });
    const url = window.URL.createObjectURL(resp.data as Blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${tableKey}_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    message.success('已导出, 记录存入 工具→导入档案→页面导出');
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? '导出失败');
  }
}

export interface PresetTableProps<T> extends TableProps<T> {
  /** localStorage 键, 每张表唯一 (如 "orders" / "alipay") */
  tableKey: string;
  /** 可选: 内置快捷按钮 (name + 要显示的字段 key); 不传则只有[全部字段]+[管理按钮] */
  presetDefaults?: { name: string; fields: string[] }[];
}

export default function PresetTable<T extends object = any>({
  tableKey,
  presetDefaults = [],
  columns = [],
  title,
  ...rest
}: PresetTableProps<T>) {
  const [visibleKeys, setVisibleKeys] = useState<string[] | null>(null);
  const cols = columns as any[];

  const renderTitle = (data: readonly T[]): ReactNode => (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
      }}
    >
      <span>{typeof title === 'function' ? title(data as T[]) : (title ?? null)}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
        <Button
          size="small"
          icon={<FileExcelOutlined />}
          onClick={() => exportToExcel(tableKey, cols, (rest.dataSource ?? []) as readonly T[])}
          title="导出当前页面数据为 Excel (记录自动存档)"
        >
          导出Excel
        </Button>
        <FieldPresetBar
          tableKey={tableKey}
          allFields={fieldsFromColumns(cols)}
          defaults={presetDefaults}
          onChange={setVisibleKeys}
        />
      </span>
    </div>
  );

  return (
    <Table<T>
      {...rest}
      columns={applyPreset(cols, visibleKeys) as TableProps<T>['columns']}
      title={renderTitle}
    />
  );
}
