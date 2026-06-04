import { Alert, Table, Tag, Typography } from 'antd';
import type { FactorySheet, FactorySheetMaterial } from '../api/orders';

/**
 * 工厂「下单图」卡片 — 发给工厂沟通用的制单图。
 * 版式参照实际下单图: 产品线条图 + 绿色标题 + 材质/尺寸/地址/时间 + 单号。
 * 同时用于「订单详情→制单图」页和「千牛截图→生成下单图」弹窗。
 */
export default function FactorySheetCard({ data }: { data: FactorySheet }) {
  const green = '#1a7a3c';
  const extraAccessories = data.materials.filter((m) => m.source === '客户备注');

  return (
    <div
      className="factory-sheet-card"
      style={{ background: 'white', padding: 20, border: '1px solid #e8e8e8', borderRadius: 6 }}
    >
      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
        {/* 产品线条图 / 尺寸图 */}
        {data.image_url ? (
          <img
            src={data.image_url}
            alt="产品图"
            style={{ width: 240, maxHeight: 240, objectFit: 'contain', border: '1px solid #f0f0f0' }}
          />
        ) : (
          <div
            style={{
              width: 240, height: 180, border: '1px dashed #ccc', borderRadius: 4,
              display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb',
            }}
          >
            无产品图
          </div>
        )}

        {/* 标题 + 规格说明 (绿色, 对应样例右侧文字) */}
        <div style={{ flex: 1 }}>
          <Typography.Title level={4} style={{ color: green, margin: 0, fontWeight: 700 }}>
            畔色木作 {data.sheet_title}
          </Typography.Title>
          {data.material_desc && (
            <div style={{ color: green, fontSize: 15, marginTop: 8, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
              {data.material_desc}
            </div>
          )}
          {data.remark && (
            <div style={{ color: green, fontSize: 15, marginTop: 4, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
              {data.remark}
            </div>
          )}
          {data.is_custom_variant && (
            <Tag color="orange" style={{ marginTop: 8 }}>
              尺寸定制 {data.dimension_changes
                ? Object.entries(data.dimension_changes).map(([k, v]) => `${k}=${String(v)}`).join(' ')
                : ''}
            </Tag>
          )}
        </div>
      </div>

      {/* 材质 / 尺寸 / 地址 / 时间 — 对应样例下方加粗信息块 */}
      <div style={{ marginTop: 16, fontSize: 15, lineHeight: 2 }}>
        <div>
          <b>材质：</b>
          {data.product_name ?? '-'}
          {data.product_code && <span style={{ color: '#999', marginLeft: 8 }}>({data.product_code})</span>}
        </div>
        <div>
          <b>尺寸：</b>
          <span style={{ color: green, fontWeight: 600 }}>{data.sku ?? data.dimension_desc ?? '-'}</span>
          <span style={{ marginLeft: 12 }}>数量 {data.qty} 件</span>
        </div>
        <div>
          <b>地址：</b>
          {[data.customer_name, data.customer_phone, data.customer_address].filter(Boolean).join('，') || '-'}
        </div>
        <div>
          <b>下单时间：</b>{data.order_date ?? '-'}
          <b style={{ marginLeft: 20 }}>发货时间：</b>{data.ship_date ?? '-'}
        </div>
        <div style={{ color: '#888', fontSize: 13, marginTop: 4, fontFamily: 'monospace' }}>
          {data.order_no}
        </div>
      </div>

      {/* 客户备注新增配件 — 醒目提示工厂额外备料 (业务需求: 截图备注里的新配件) */}
      {extraAccessories.length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 16 }}
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
      <Typography.Title level={5} style={{ marginTop: 20, marginBottom: 8 }}>
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
          bordered
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
            { title: '单件用量', dataIndex: 'qty_per_product', width: 90, align: 'right' },
            { title: `×${data.qty}件总量`, dataIndex: 'total_qty', width: 100, align: 'right',
              render: (v: string) => <strong>{v}</strong> },
            { title: '单位', dataIndex: 'unit', width: 56 },
            { title: '备注', dataIndex: 'spec', ellipsis: true },
          ]}
        />
      )}
      <style>{`.extra-accessory-row { background: #fff7e6; }`}</style>
    </div>
  );
}
