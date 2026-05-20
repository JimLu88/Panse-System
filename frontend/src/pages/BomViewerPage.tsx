import { Alert, Collapse, Space, Spin, Table, Tag, Typography } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { BomLineRow, listBomForProduct } from '../api/client';

export default function BomViewerPage() {
  const { productCode = '' } = useParams<{ productCode: string }>();
  const { data, isLoading, isError } = useQuery({
    queryKey: ['bom', productCode],
    queryFn: () => listBomForProduct(productCode),
    enabled: !!productCode,
  });

  const columns = [
    {
      title: '物料编码',
      dataIndex: 'material_code',
      width: 110,
      render: (v: string) =>
        v.startsWith('AC-') && parseInt(v.slice(3), 10) >= 1000 ? (
          <Tag color="orange">{v}</Tag>
        ) : (
          v
        ),
    },
    { title: '物料名', dataIndex: 'material_name', ellipsis: true },
    { title: '单产品用量', dataIndex: 'qty_per_product', width: 110 },
    { title: '单位', dataIndex: 'unit', width: 70 },
  ];

  if (!productCode) {
    return (
      <Alert
        type="warning"
        message="缺少产品编码"
        description={
          <span>
            从 <Link to="/products">产品总表</Link> 点「查看 BOM」进来。
          </span>
        }
      />
    );
  }

  if (isLoading) return <Spin />;
  if (isError) return <Alert type="error" message="加载 BOM 失败" />;

  const groups = data ?? [];

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          BOM · {productCode}
        </Typography.Title>
        <Link to="/products">← 返回产品总表</Link>
      </Space>

      {groups.length === 0 ? (
        <Alert
          type="info"
          message="该产品暂无 BOM 数据"
          description="冷启动时如果物料缺失，对应 BOM 行会被跳过；可以先到「物料单价库」补料后重导。"
        />
      ) : (
        <Collapse
          defaultActiveKey={groups[0]?.sku_code ? [groups[0].sku_code] : []}
          items={groups.map((g) => ({
            key: g.sku_code || g.sku || 'default',
            label: (
              <Space>
                <Tag color="blue">{g.sku_code}</Tag>
                <span>{g.sku}</span>
                <span style={{ color: '#888' }}>({g.lines.length} 项)</span>
              </Space>
            ),
            children: (
              <Table<BomLineRow>
                rowKey="id"
                dataSource={g.lines}
                columns={columns as any}
                size="small"
                pagination={false}
              />
            ),
          }))}
        />
      )}
    </Space>
  );
}
