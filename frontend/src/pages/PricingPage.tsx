/**
 * 定价总表 (#3): 展示导入的全部 SKU 四档售价 + 成本/毛利.
 */
import { useState } from 'react';
import { Card, Input, Select, Space, Table, Typography } from 'antd';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { PricingSku, listPricingSkus } from '../api/client';

const PAGE_SIZE = 50;

function money(v: number | null) {
  return v === null || v === undefined ? '-' : `¥${Number(v).toLocaleString()}`;
}

export default function PricingPage() {
  const [q, setQ] = useState('');
  const [sizeCategory, setSizeCategory] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);

  const { data, isFetching } = useQuery({
    queryKey: ['pricing-skus', q, sizeCategory, page],
    queryFn: () =>
      listPricingSkus({
        q: q || undefined,
        size_category: sizeCategory,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>
        定价总表
      </Typography.Title>
      <Card size="small">
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="搜产品编码 / SKU 编码 / 描述"
            style={{ width: 280 }}
            onSearch={(v) => {
              setQ(v);
              setPage(1);
            }}
          />
          <Select
            allowClear
            placeholder="大小分类"
            style={{ width: 140 }}
            value={sizeCategory}
            onChange={(v) => {
              setSizeCategory(v);
              setPage(1);
            }}
            options={[
              { value: '小型', label: '小型' },
              { value: '中型', label: '中型' },
              { value: '大型', label: '大型' },
            ]}
          />
        </Space>
      </Card>
      <Table<PricingSku>
        size="small"
        rowKey="id"
        loading={isFetching}
        dataSource={data?.items ?? []}
        scroll={{ x: 1100 }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: setPage,
          showSizeChanger: false,
        }}
        columns={[
          { title: '产品编码', dataIndex: 'product_code', fixed: 'left', width: 110 },
          { title: 'SKU 编码', dataIndex: 'sku_code', fixed: 'left', width: 120 },
          { title: '描述', dataIndex: 'sku', width: 160, ellipsis: true },
          { title: '分类', dataIndex: 'size_category', width: 70 },
          { title: '标价', dataIndex: 'list_price', width: 90, render: money },
          { title: '日常价', dataIndex: 'daily_price', width: 90, render: money },
          { title: '小促', dataIndex: 'small_promo', width: 90, render: money },
          { title: '中促', dataIndex: 'mid_promo', width: 90, render: money },
          { title: '大促', dataIndex: 'big_promo', width: 90, render: money },
          {
            title: '毛利率',
            dataIndex: 'gross_margin_rate',
            width: 90,
            render: (v: number | null) =>
              v === null || v === undefined ? '-' : `${(Number(v) * 100).toFixed(1)}%`,
          },
          { title: '会计成本', dataIndex: 'accounting_cost', width: 100, render: money },
          { title: '物理成本', dataIndex: 'physical_cost', width: 100, render: money },
        ]}
      />
    </Space>
  );
}
