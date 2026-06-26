/**
 * 响应式表格壳 (用户拍板 2026-06-26):
 *   - 桌面 (≥768px / AntD md 及以上): 原样渲染调用方传入的 <Table>/<PresetTable> —— 零改动、零风险。
 *   - 手机 (<768px): 改渲染卡片列表 (MobileCards 里的 MetricCard/CatalogCard/StatusCard 或自定义)。
 *
 * 用法:
 *   <ResponsiveTable
 *     desktop={<Table ... />}          // 原桌面表格, 整段照搬
 *     data={rows} rowKey={(r)=>r.id}
 *     renderCard={(r)=> <CatalogCard ... />}
 *     loading={isLoading}
 *   />
 * 不传 renderCard 或非手机时, 一律走 desktop, 不影响任何既有页面。
 */
import { type Key, type ReactNode } from 'react';
import { Empty, Grid, Spin } from 'antd';

export interface ResponsiveTableProps<T> {
  /** 桌面端原样渲染 (现有 <Table>/<PresetTable> 整段) */
  desktop: ReactNode;
  /** 手机端卡片数据源 */
  data: readonly T[];
  /** 行 key */
  rowKey: (row: T, index: number) => Key;
  /** 手机端每行渲染成卡片 */
  renderCard: (row: T, index: number) => ReactNode;
  /** 加载态 (手机端显示 Spin) */
  loading?: boolean;
  /** 可选: 卡片列表顶部 (搜索/筛选/统计条等), 仅手机端显示 */
  mobileHeader?: ReactNode;
  /** 可选: 空数据文案 */
  emptyText?: ReactNode;
}

export default function ResponsiveTable<T>({
  desktop, data, rowKey, renderCard, loading, mobileHeader, emptyText,
}: ResponsiveTableProps<T>) {
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;   // === false 防桌面首屏闪 (与 App.tsx 同口径)

  if (!isMobile) return <>{desktop}</>;

  return (
    <div>
      {mobileHeader}
      {loading ? (
        <div style={{ padding: 40, display: 'flex', justifyContent: 'center' }}><Spin /></div>
      ) : data.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText ?? '暂无数据'} style={{ padding: '32px 0' }} />
      ) : (
        data.map((row, i) => <div key={rowKey(row, i)}>{renderCard(row, i)}</div>)
      )}
    </div>
  );
}
