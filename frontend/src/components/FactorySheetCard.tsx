import type { ReactNode } from 'react';
import { Alert, Table, Tag, Typography } from 'antd';
import type { FactorySheet, FactorySheetMaterial } from '../api/orders';
import { CUTE_IMG } from './ProductThumb';

// 图片加载失败(如 image_url 是商品页链接而非图片)→ 占位, 不出现裂图
const swapToPlaceholder = (e: { currentTarget: HTMLImageElement }) => {
  if (e.currentTarget.src !== CUTE_IMG) e.currentTarget.src = CUTE_IMG;
};

// 2.0000 → 2 (用户要求: 数量整数化, 别带一串零)
const intQty = (v: unknown): string => {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return String(v ?? '');
  return String(n);
};

/**
 * 工厂「下单图」卡片 — 方向二「图文卡片」版式 (用户拍板 2026-06-12)。
 * 品牌绿横幅 + 左图(SKU尺寸图为主)右信息(规格尺寸大字), 时间只写下单/发货日期。
 * 同时用于「订单详情→制单图」页和「千牛截图→生成下单图」弹窗。
 */
const galleryUrl = (p: string) => `/api/gallery/file?path=${encodeURIComponent(p)}&max_edge=1600`;

const GREEN = '#1a7a3c';

export default function FactorySheetCard({ data }: { data: FactorySheet }) {
  const extraAccessories = data.materials.filter((m) => m.source === '客户备注');
  // 图库优先 (2026-06-11): SKU 尺寸图 + 主图; 图库没有时回退淘宝 image_url
  const skuImg = (data as any).sku_image ? galleryUrl((data as any).sku_image) : null;
  const mainImg = (data as any).gallery_main_image
    ? galleryUrl((data as any).gallery_main_image) : data.image_url;
  const sizeText = (data as any).size_info ?? data.sku ?? data.dimension_desc ?? '-';

  const infoRow = (label: string, content: ReactNode) => (
    <div style={{ display: 'flex', padding: '7px 0', borderBottom: '1px dashed #e5e7eb', fontSize: 14 }}>
      <div style={{ width: 64, color: '#999', flexShrink: 0 }}>{label}</div>
      <div style={{ flex: 1 }}>{content}</div>
    </div>
  );

  return (
    <div
      className="factory-sheet-card"
      style={{
        background: 'white', borderRadius: 14, overflow: 'hidden',
        boxShadow: '0 2px 14px rgba(0,0,0,.12)',
      }}
    >
      {/* 品牌横幅 */}
      <div
        style={{
          background: `linear-gradient(95deg, ${GREEN}, #2f9b58)`, color: '#fff',
          padding: '14px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}
      >
        <div style={{ fontSize: 18, fontWeight: 700 }}>畔色木作 · 工厂下单图</div>
        <div style={{ background: 'rgba(255,255,255,.18)', borderRadius: 99, padding: '4px 14px', fontSize: 13 }}>
          {data.sheet_title}
        </div>
      </div>

      {/* 左图 右信息 */}
      <div style={{ display: 'flex', gap: 18, padding: '18px 20px' }}>
        <div style={{ width: 230, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
          {skuImg ? (
            <div>
              <img
                src={skuImg}
                alt="SKU尺寸图"
                onError={swapToPlaceholder}
                style={{ width: 230, maxHeight: 260, objectFit: 'contain', border: '1px solid #f0f0f0', borderRadius: 8 }}
              />
              <div style={{ fontSize: 12, color: GREEN, textAlign: 'center', marginTop: 2 }}>SKU 尺寸图（工厂按此做）</div>
            </div>
          ) : null}
          {mainImg ? (
            <img
              src={mainImg}
              alt="产品图"
              onError={swapToPlaceholder}
              style={{ width: 230, maxHeight: skuImg ? 120 : 230, objectFit: 'contain', border: '1px solid #f0f0f0', borderRadius: 8 }}
            />
          ) : null}
          {!skuImg && !mainImg && (
            <div
              style={{
                width: 230, height: 180, border: '1px dashed #ccc', borderRadius: 8,
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb',
              }}
            >
              无产品图
            </div>
          )}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ color: '#888', fontSize: 13 }}>规格尺寸</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: GREEN, lineHeight: 1.35, margin: '2px 0 4px' }}>
            {sizeText}
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: '#5b8c6e', marginBottom: 8 }}>
            数量 {data.qty} 件
          </div>

          {infoRow('产品', (
            <span>
              {data.product_name ?? '-'}
              {data.product_code && <span style={{ color: '#999', marginLeft: 8 }}>({data.product_code})</span>}
            </span>
          ))}
          {infoRow('收件', (
            <span>{[data.customer_name, data.customer_phone, data.customer_address].filter(Boolean).join('，') || '-'}</span>
          ))}
          {/* 用户拍板 (2026-06-12): 只写下单/发货日期, 不标注"+25天" */}
          {infoRow('时间', (
            <span>下单 {data.order_date ?? '-'} → 发货 <b>{data.ship_date ?? '-'}</b></span>
          ))}
          {/* 图4 (2026-06-12): 下单图先写主材, 再写辅材 */}
          {(data as any).main_material && infoRow('主材', (
            <span style={{ color: '#222', fontWeight: 600, whiteSpace: 'pre-wrap' }}>{(data as any).main_material}</span>
          ))}
          {(data as any).aux_material && infoRow('辅材', (
            <span style={{ color: '#555', whiteSpace: 'pre-wrap' }}>{(data as any).aux_material}</span>
          ))}
          {data.material_desc && infoRow('说明', (
            <span style={{ color: '#888', whiteSpace: 'pre-wrap' }}>{data.material_desc}</span>
          ))}
          {data.remark && infoRow('备注', (
            <span style={{ color: '#a8743a', whiteSpace: 'pre-wrap' }}>{data.remark}</span>
          ))}
          {(data as any).production_note && infoRow('生产备注', (
            <span style={{ color: '#a8743a', whiteSpace: 'pre-wrap' }}>{(data as any).production_note}</span>
          ))}

          <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {data.is_custom_variant && (
              <Tag color="orange" style={{ borderRadius: 99 }}>
                尺寸定制 {data.dimension_changes
                  ? Object.entries(data.dimension_changes).map(([k, v]) => `${k}=${String(v)}`).join(' ')
                  : ''}
              </Tag>
            )}
            <span style={{ color: '#bbb', fontSize: 12, fontFamily: 'monospace' }}>{data.order_no}</span>
          </div>
        </div>
      </div>

      {/* 客户备注新增配件 — 醒目提示工厂额外备料 (业务需求: 截图备注里的新配件) */}
      {extraAccessories.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ margin: '0 20px 12px' }}
          message={`客户备注新增配件 ${extraAccessories.length} 项 — 请额外备料`}
          description={
            <ul style={{ marginBottom: 0, paddingLeft: 18 }}>
              {extraAccessories.map((m) => (
                <li key={m.material_code}>
                  <b>{m.material_name}</b> × {m.total_qty}{m.unit ?? ''}
                  {m.note && <span style={{ color: '#a8743a' }}>（{m.note}）</span>}
                </li>
              ))}
            </ul>
          }
        />
      )}

      {/* BOM 物料明细 (内部配件采购用, 工厂版可忽略) */}
      <div style={{ padding: '0 20px 18px' }}>
        <Typography.Title level={5} style={{ margin: '4px 0 8px' }}>
          物料明细 <span style={{ fontWeight: 400, fontSize: 12, color: '#999' }}>(供配件采购, 工厂备料参考)</span>
        </Typography.Title>
        {data.materials.length === 0 ? (
          <Alert type="warning" message="该 SKU 暂无 BOM, 请先在 BOM 表录入物料" />
        ) : (
          <Table<FactorySheetMaterial>
            rowKey="material_code"
            dataSource={data.materials}
            pagination={false}
            size="small"
            className="sheet-mat-table"
            rowClassName={(r) => (r.source === '客户备注' ? 'extra-accessory-row' : '')}
            columns={[
              { title: '物料编码', dataIndex: 'material_code', width: 110, render: (v: string) => <code>{v}</code> },
              {
                title: '物料名称', dataIndex: 'material_name', ellipsis: true,
                render: (v: string, r) => (
                  <span>
                    {v}
                    {r.source === '客户备注' && <Tag color="orange" style={{ marginLeft: 6 }}>客户加配</Tag>}
                  </span>
                ),
              },
              { title: '单件用量', dataIndex: 'qty_per_product', width: 90, align: 'right',
                render: (v: string) => intQty(v) },
              { title: `×${data.qty}件总量`, dataIndex: 'total_qty', width: 100, align: 'right',
                render: (v: string) => <strong>{intQty(v)}</strong> },
              { title: '单位', dataIndex: 'unit', width: 56 },
              { title: '备注', dataIndex: 'spec', ellipsis: true },
            ]}
          />
        )}
      </div>
      <style>{`
        .extra-accessory-row { background: #fff7e6; }
        .sheet-mat-table .ant-table-thead > tr > th {
          background: #fff; color: #888; border-bottom: 2px solid ${GREEN};
        }
        .sheet-mat-table .ant-table-tbody > tr:nth-child(even) > td { background: #fafcfa; }
      `}</style>
    </div>
  );
}
