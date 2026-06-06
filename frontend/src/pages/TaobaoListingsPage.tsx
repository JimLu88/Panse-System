import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Input,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  Upload,
  message,
} from 'antd';
import { InboxOutlined, LinkOutlined } from '@ant-design/icons';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  TaobaoListing,
  importTaobaoExport,
  backfillOrdersFromListings,
  listTaobaoListings,
  updateTaobaoListing,
} from '../api/client';
import FullColumnView from '../components/FullColumnView';

const PAGE_SIZE = 100;

export default function TaobaoListingsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [matchFilter, setMatchFilter] = useState<'all' | 'matched' | 'unmatched'>('all');
  const [page, setPage] = useState(1);
  const [viewMode, setViewMode] = useState<'curated' | 'full'>('curated');
  const [shop, setShop] = useState<string>('自动');  // 导入归属店铺 (分店统计)

  const matchedParam = matchFilter === 'all' ? undefined : matchFilter === 'matched';

  const { data, isFetching } = useQuery({
    queryKey: ['taobao-listings', q, matchedParam, page],
    queryFn: () =>
      listTaobaoListings({
        q: q || undefined,
        matched: matchedParam,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });

  const importMut = useMutation({
    mutationFn: (file: File) => importTaobaoExport(file, shop === '自动' ? undefined : shop),
    onSuccess: (r) => {
      message.success(
        `导入完成：新增 ${r.inserted}，更新 ${r.updated}，自动匹配系统SKU ${r.matched}/${r.total}`,
      );
      if (r.warnings?.length) message.warning(r.warnings.join('；'));
      qc.invalidateQueries({ queryKey: ['taobao-listings'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导入失败'),
  });

  const backfillMut = useMutation({
    mutationFn: () => backfillOrdersFromListings(),
    onSuccess: (r) => {
      message.success(`回填完成：扫描 ${r.scanned} 单，更新 ${r.updated} 单`);
      qc.invalidateQueries({ queryKey: ['taobao-listings'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '回填失败'),
  });

  const editMut = useMutation({
    mutationFn: ({ id, sku_code }: { id: number; sku_code: string }) =>
      updateTaobaoListing(id, { sku_code }),
    onSuccess: () => {
      message.success('已更新对应关系');
      qc.invalidateQueries({ queryKey: ['taobao-listings'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '更新失败'),
  });

  const columns = [
    { title: '商品ID', dataIndex: 'taobao_item_id', width: 130, render: (v: string) => <code style={{ fontSize: 11 }}>{v}</code> },
    { title: '淘宝SKU', dataIndex: 'taobao_sku_id', width: 130, render: (v: string | null) => v ? <code style={{ fontSize: 11 }}>{v}</code> : '-' },
    { title: '宝贝标题', dataIndex: 'title', ellipsis: true },
    { title: '规格', dataIndex: 'sku_spec', width: 180, ellipsis: true },
    { title: '商家编码', dataIndex: 'merchant_code', width: 150, render: (v: string | null) => v ?? '-' },
    {
      title: 'SKU价格', dataIndex: 'sku_price', width: 100, align: 'right' as const,
      render: (v: string | null) => (v ? `¥${v}` : '-'),
    },
    {
      title: '系统SKU编码', dataIndex: 'sku_code', width: 170,
      render: (v: string | null, row: TaobaoListing) => (
        <Typography.Text
          editable={{
            onChange: (val) => {
              const next = val.trim();
              if (next && next !== (row.sku_code ?? '')) editMut.mutate({ id: row.id, sku_code: next });
            },
            tooltip: '点击编辑系统SKU编码',
          }}
          type={v ? undefined : 'secondary'}
        >
          {v ?? '未匹配'}
        </Typography.Text>
      ),
    },
    {
      title: '状态', dataIndex: 'matched', width: 90,
      render: (m: boolean) =>
        m ? <Tag color="green" icon={<LinkOutlined />}>已匹配</Tag> : <Tag>未匹配</Tag>,
    },
    {
      title: '店铺', dataIndex: 'shop', width: 90,
      render: (v: string | null) => (v ? <Tag color="blue">{v}</Tag> : '-'),
    },
  ];

  const matchRate =
    data && data.total > 0 ? Math.round((data.matched / data.total) * 100) : 0;

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }} align="start">
        <Typography.Title level={4} style={{ margin: 0 }}>淘宝商品对应表</Typography.Title>
      </Space>

      <Segmented
        value={viewMode}
        onChange={(v) => setViewMode(v as 'curated' | 'full')}
        options={[
          { label: '精选视图', value: 'curated' },
          { label: '全部列', value: 'full' },
        ]}
      />
      {viewMode === 'full' && <FullColumnView entity="taobao_listing" />}
      {viewMode === 'curated' && (<>

      <Alert
        type="info"
        showIcon
        message="如何下载 & 导入"
        description={
          <>
            从淘宝后台 <b>商品管理 → 批量导出</b> 下载「商品导出」Excel。表内必须包含
            <b> 商品Id、skuId、销售属性(颜色分类)</b>，以及<b>两个「商家编码」列</b>
            （第 1 个 = 14 位产品编码，第 2 个 = 16 位 <b>SKU 编码</b>，系统取第 2 个）。
            两个店铺（畔色 / 孚格）请<b>各自导出、分别上传</b>，并在下方选好对应店铺（用于分店统计）。
            导入按商家编码自动匹配系统定价 SKU；未匹配的可在表内点「系统SKU编码」手动补填。
          </>
        }
      />

      <Card size="small">
        <Space direction="vertical" style={{ width: '100%' }} size="small">
          <Space wrap>
            <span>归属店铺：</span>
            <Segmented
              value={shop}
              onChange={(v) => setShop(v as string)}
              options={['自动', '畔色店', '孚格店']}
            />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              「自动」= 按文件名（孚格… / 畔色…）推断
            </Typography.Text>
            <Button
              onClick={() => backfillMut.mutate()}
              loading={backfillMut.isPending}
            >
              用对应表回填订单店铺/编码
            </Button>
          </Space>
          <Upload.Dragger
            accept=".xlsx,.xls"
            maxCount={1}
            showUploadList={false}
            disabled={importMut.isPending}
            beforeUpload={(file) => {
              importMut.mutate(file as File);
              return false;
            }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">点击或拖拽淘宝商品导出 Excel 到此处导入</p>
            <p className="ant-upload-hint">支持 .xlsx / .xls；重复导入会按 商品ID+skuId 更新已有记录</p>
          </Upload.Dragger>
        </Space>
      </Card>

      <Card size="small">
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜 商品ID / skuId / 商家编码 / 标题"
              style={{ width: 320 }}
              onSearch={(v) => { setQ(v); setPage(1); }}
              onChange={(e) => { if (!e.target.value) { setQ(''); setPage(1); } }}
            />
            <Segmented
              value={matchFilter}
              onChange={(v) => { setMatchFilter(v as typeof matchFilter); setPage(1); }}
              options={[
                { label: '全部', value: 'all' },
                { label: '已匹配', value: 'matched' },
                { label: '未匹配', value: 'unmatched' },
              ]}
            />
          </Space>
          {data && (
            <Tooltip title="已匹配系统SKU的记录占比">
              <Tag color={matchRate >= 80 ? 'green' : matchRate >= 40 ? 'orange' : 'red'}>
                匹配率 {matchRate}%（{data.matched}/{data.total}）
              </Tag>
            </Tooltip>
          )}
        </Space>
      </Card>

      <Table<TaobaoListing>
        rowKey="id"
        size="small"
        loading={isFetching}
        dataSource={data?.items ?? []}
        columns={columns}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
        scroll={{ x: 1200 }}
      />
      </>)}
    </Space>
  );
}
