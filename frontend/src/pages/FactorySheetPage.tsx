import { useEffect } from 'react';
import { Alert, Button, Descriptions, Space, Spin, Table, Tag, Typography } from 'antd';
import { PrinterOutlined, WarningOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FactorySheetMaterial, FactorySheetWarning, getFactorySheet } from '../api/client';

export default function FactorySheetPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const nav = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ['factory-sheet', orderId],
    queryFn: () => getFactorySheet(Number(orderId)),
    enabled: !!orderId,
  });

  useEffect(() => {
    document.title = data ? `制单图 - ${data.sheet_title}` : '制单图';
  }, [data]);

  if (isLoading) return <Spin />;
  if (error || !data) return <Alert type="error" message="加载失败" />;

  const hasError = data.warnings.some((w) => w.severity === 'error');

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space style={{ justifyContent: 'space-between', width: '100%' }} className="no-print">
          <Button onClick={() => nav('/orders')}>← 返回订单</Button>
          <Button
            type="primary"
            icon={<PrinterOutlined />}
            disabled={hasError}
            onClick={() => window.print()}
          >
            打印制单图
          </Button>
        </Space>

        {hasError && (
          <Alert
            type="error"
            showIcon
            icon={<WarningOutlined />}
            message="存在严重问题, 请先解决再打印"
            description={
              <ul style={{ marginBottom: 0 }}>
                {data.warnings
                  .filter((w) => w.severity === 'error')
                  .map((w) => <li key={w.code}>{w.message}</li>)}
              </ul>
            }
          />
        )}
        {data.warnings
          .filter((w) => w.severity !== 'error')
          .map((w) => (
            <Alert key={w.code} type="warning" showIcon message={w.message} />
          ))}

        <div className="factory-sheet" style={{ background: 'white', padding: 24, border: '1px solid #ddd' }}>
          <Space style={{ justifyContent: 'space-between', width: '100%', marginBottom: 16 }}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              畔色木作 制单图
            </Typography.Title>
            <Typography.Text strong style={{ color: '#3f8600' }}>
              {data.sheet_title}
            </Typography.Text>
          </Space>

          {data.is_custom_variant && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={
                <>
                  <Tag color="orange">尺寸定制</Tag>
                  <code>{data.sku_code}</code>
                  {data.dimension_changes && (
                    <span style={{ marginLeft: 12 }}>
                      变更: {Object.entries(data.dimension_changes).map(([k, v]) =>
                        <Tag key={k}>{k} = {String(v)}</Tag>
                      )}
                    </span>
                  )}
                </>
              }
            />
          )}

          <Space size="middle" align="start" style={{ width: '100%' }}>
            {data.image_url && (
              <img
                src={data.image_url}
                alt="产品图"
                style={{ width: 220, border: '1px solid #eee' }}
              />
            )}
            <Descriptions column={1} size="small" bordered style={{ flex: 1 }}>
              <Descriptions.Item label="产品编码">
                <code>{data.product_code ?? '-'}</code>
              </Descriptions.Item>
              <Descriptions.Item label="产品名称">{data.product_name ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="SKU">{data.sku ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="SKU 编码">
                <code style={{ fontSize: 12 }}>{data.sku_code ?? '-'}</code>
              </Descriptions.Item>
              <Descriptions.Item label="数量">
                <Tag color="blue">{data.qty} 件</Tag>
              </Descriptions.Item>
            </Descriptions>
          </Space>

          <Descriptions column={2} size="small" bordered style={{ marginTop: 16 }}>
            <Descriptions.Item label="订单编号" span={2}>
              <code>{data.order_no}</code>
            </Descriptions.Item>
            <Descriptions.Item label="客户姓名">{data.customer_name ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="联系电话">{data.customer_phone ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="收货地址" span={2}>
              {data.customer_address ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="下单日期">{data.order_date ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="发货日期">{data.ship_date ?? '-'}</Descriptions.Item>
            {data.remark && (
              <Descriptions.Item label="备注" span={2}>{data.remark}</Descriptions.Item>
            )}
          </Descriptions>

          <Typography.Title level={5} style={{ marginTop: 24, marginBottom: 8 }}>
            BOM 物料明细 (业务需求 §1: 自动同步, 方便配件采购)
          </Typography.Title>

          {data.materials.length === 0 ? (
            <Alert type="warning" message="该 SKU 暂无 BOM, 请先在 BOM 表录入物料后再生成制单图" />
          ) : (
            <Table<FactorySheetMaterial>
              rowKey="material_code"
              dataSource={data.materials}
              pagination={false}
              size="small"
              bordered
              columns={[
                { title: '物料编码', dataIndex: 'material_code', width: 110,
                  render: (v: string) => <code>{v}</code> },
                { title: '物料名称', dataIndex: 'material_name', ellipsis: true },
                { title: '单产品用量', dataIndex: 'qty_per_product', width: 100, align: 'right' },
                {
                  title: `× ${data.qty} 件总量`,
                  dataIndex: 'total_qty',
                  width: 110,
                  align: 'right',
                  render: (v: string) => <strong>{v}</strong>,
                },
                { title: '单位', dataIndex: 'unit', width: 60 },
                { title: '备注', dataIndex: 'spec', ellipsis: true },
              ]}
            />
          )}
        </div>
      </Space>

      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { background: white; }
          .factory-sheet { border: none !important; padding: 0 !important; }
        }
      `}</style>
    </div>
  );
}
