/**
 * 销售预测 + 备货建议 + 滞销 (Phase 4, 业务需求 7/8).
 *
 * 三个卡片:
 *   - 未来 30 天 SKU 预测销量 (移动平均 + 20% 安全系数)
 *   - 智能提前备货建议 (物料 lead_time 倒推何时下单)
 *   - 滞销分类 (长期未售 / 超大库存)
 */
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  fetchForecast30d,
  fetchSlowMoving,
  fetchStockAdvice,
  upsertSemiFinished,
} from '../api/client';
import ProductThumb from '../components/ProductThumb';
import RefillCallout from '../components/RefillCallout';

// 导出当前数据为 Excel (复用页面导出端点, 记录进 资料存档库→页面导出)
async function exportPageXlsx(title: string, columns: { key: string; title: string }[], rows: any[]) {
  if (!rows.length) { message.warning('没有可导出的数据'); return; }
  try {
    const resp = await api.post('/api/exports/page', { title, columns, rows }, { responseType: 'blob' });
    const url = window.URL.createObjectURL(resp.data as Blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${title}_${new Date().toISOString().slice(0, 10)}.xlsx`;
    document.body.appendChild(a); a.click(); a.remove();
    window.URL.revokeObjectURL(url);
    message.success('已导出 (记录存 资料存档库→页面导出)');
  } catch (e: any) {
    message.error(e?.response?.data?.detail ?? '导出失败');
  }
}

// 某产品的 SKU 列表按 60 天销量占比拆出 30 天预测
function skuForecast(r: any, s: any): number {
  const total = (r.skus ?? []).reduce((sum: number, x: any) => sum + (x.qty_60d || 0), 0) || 1;
  return Math.round(((s.qty_60d || 0) / total) * (r.forecast_30d || 0));
}

// SKU 构成标签 (按产品聚合后的明细; 定制咨询类 SKU 也归并在所属产品下)
function skuTags(r: any) {
  const skus: { sku: string; qty_60d: number }[] = r.skus ?? [];
  if (!skus.length) return <Typography.Text type="secondary">—</Typography.Text>;
  return (
    <Space size={4} wrap>
      {skus.slice(0, 4).map((s) => (
        <Tag key={s.sku} style={{ fontSize: 11 }}>{s.sku} ×{s.qty_60d}</Tag>
      ))}
      {skus.length > 4 && <Tag style={{ fontSize: 11 }}>+{skus.length - 4} 个</Tag>}
    </Space>
  );
}

// 产品列: 图 + 名称 + 编码 (没名没图的行至少有编码兜底)
function productCell(r: any) {
  return (
    <Space size={8}>
      <ProductThumb src={r.image_url || null} size={40} />
      <span>
        <div>{r.product_name || <Typography.Text type="secondary">—</Typography.Text>}</div>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.product_code || '—'}</Typography.Text>
      </span>
    </Space>
  );
}


export default function ForecastPage() {
  return (
    <>
      <RefillCallout />
      <Tabs items={[
        { key: 'forecast', label: '销售预测 (30 天)', children: <ForecastTab /> },
        { key: 'advice', label: '备货建议', children: <AdviceTab /> },
        { key: 'slow', label: '滞销分类', children: <SlowMovingTab /> },
      ]} />
    </>
  );
}

function ForecastTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['forecast-30d'], queryFn: fetchForecast30d,
  });
  const exportForecast = () => {
    const cols = [
      { key: 'product_name', title: '产品' }, { key: 'product_code', title: '产品编码' },
      { key: 'sku', title: 'SKU' }, { key: 'qty_60d', title: 'SKU 60天销量' },
      { key: 'sku_forecast', title: 'SKU 预测30天' },
      { key: 'forecast_30d', title: '产品预测30天' }, { key: 'last_60d_total', title: '产品60天总销' },
      { key: 'avg_daily', title: '产品日均' },
    ];
    const rows: any[] = [];
    for (const r of (data?.forecast ?? [])) {
      const skus = (r as any).skus ?? [];
      if (!skus.length) {
        rows.push({ product_name: r.product_name, product_code: r.product_code, sku: '(无SKU明细)',
          qty_60d: null, sku_forecast: null, forecast_30d: r.forecast_30d, last_60d_total: r.last_60d_total, avg_daily: r.avg_daily });
      } else {
        for (const s of skus) rows.push({ product_name: r.product_name, product_code: r.product_code, sku: s.sku,
          qty_60d: s.qty_60d, sku_forecast: skuForecast(r, s), forecast_30d: r.forecast_30d, last_60d_total: r.last_60d_total, avg_daily: r.avg_daily });
      }
    }
    exportPageXlsx('未来30天销售预测', cols, rows);
  };
  return (
    <Card size="small" title="未来 30 天预测销量 (基于过去 60 天移动平均 × 1.2) · 点行展开看该产品全部 SKU"
      extra={<Button icon={<DownloadOutlined />} onClick={exportForecast} disabled={!data?.forecast?.length}>导出 Excel</Button>}>
      <Table
        size="small" loading={isLoading}
        rowKey="product_code"
        dataSource={data?.forecast ?? []}
        expandable={{
          rowExpandable: (r: any) => ((r.skus?.length ?? 0) > 0),
          expandedRowRender: (r: any) => (
            <Table size="small" pagination={false} rowKey={(s: any) => s.sku}
              dataSource={r.skus ?? []}
              columns={[
                { title: 'SKU', dataIndex: 'sku' },
                { title: '过去 60 天销量', dataIndex: 'qty_60d', width: 150 },
                { title: '预测 30 天 (按占比)', width: 160, render: (_: any, s: any) => <Tag color="blue">{skuForecast(r, s)}</Tag> },
              ]} />
          ),
        }}
        columns={[
          { title: '产品', width: 240, render: (_: any, r: any) => productCell(r),
            sorter: (a: any, b: any) => String(a.product_name ?? a.product_code).localeCompare(String(b.product_name ?? b.product_code)) },
          { title: 'SKU 构成 (60天销量)', render: (_: any, r: any) => skuTags(r) },
          { title: '过去 60 天总销', dataIndex: 'last_60d_total', width: 130,
            sorter: (a: any, b: any) => (a.last_60d_total ?? 0) - (b.last_60d_total ?? 0) },
          { title: '日均', dataIndex: 'avg_daily', width: 100 },
          { title: '未来 30 天预测', dataIndex: 'forecast_30d', width: 130,
            render: (v: number) => <Tag color="blue">{v}</Tag>,
          },
        ]}
        pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
      />
    </Card>
  );
}

function AdviceTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['stock-advice'], queryFn: fetchStockAdvice,
  });
  const qc = useQueryClient();
  const saveSemi = async (group: string, field: 'on_hand_qty' | 'in_production_qty', val: number) => {
    try {
      await upsertSemiFinished(group, { [field]: val });
      qc.invalidateQueries({ queryKey: ['stock-advice'] });
      message.success('已保存');
    } catch {
      message.error('保存半成品库存失败');
    }
  };
  const exportProducts = () => exportPageXlsx('备货建议-产能缺口', [
    { key: 'product_name', title: '产品' }, { key: 'product_code', title: '产品编码' },
    { key: 'forecast_30d', title: '预测30天' }, { key: 'in_stock', title: '现成品库存' },
    { key: 'in_production_free', title: '备货在产(会入库)' }, { key: 'in_production_allocated', title: '客户单在产(发客户)' },
    { key: 'need_to_produce', title: '需生产' },
  ], (data?.products ?? []).map((r: any) => ({ product_name: r.product_name, product_code: r.product_code, forecast_30d: r.forecast_30d, in_stock: r.in_stock, in_production_free: r.in_production_free, in_production_allocated: r.in_production_allocated, need_to_produce: r.need_to_produce })));
  const exportMaterials = () => exportPageXlsx('备货建议-物料下单', [
    { key: 'material_code', title: '物料' }, { key: 'material_name', title: '名称' }, { key: 'need_qty', title: '需求量' },
    { key: 'have_qty', title: '现库存' }, { key: 'missing', title: '缺口' }, { key: 'lead_time_days', title: '补货周期(天)' },
    { key: 'alert_at', title: '建议下单日' }, { key: 'should_order_now', title: '现在该下单' },
  ], (data?.materials ?? []).map((r: any) => ({ material_code: r.material_code, material_name: r.material_name, need_qty: r.need_qty, have_qty: r.have_qty, missing: r.missing, lead_time_days: r.lead_time_days, alert_at: r.alert_at, should_order_now: r.should_order_now ? '是' : '否' })));
  const exportCustomMaterials = () => exportPageXlsx('备货建议-定制通用料', [
    { key: 'material_code', title: '物料' }, { key: 'material_name', title: '名称' }, { key: 'need_qty', title: '定制需求量' },
    { key: 'have_qty', title: '现库存' }, { key: 'missing', title: '缺口' }, { key: 'lead_time_days', title: '补货周期(天)' },
    { key: 'alert_at', title: '建议下单日' }, { key: 'should_order_now', title: '现在该下单' },
  ], (data?.custom_materials ?? []).map((r: any) => ({ material_code: r.material_code, material_name: r.material_name, need_qty: r.need_qty, have_qty: r.have_qty, missing: r.missing, lead_time_days: r.lead_time_days, alert_at: r.alert_at, should_order_now: r.should_order_now ? '是' : '否' })));
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon
             message="备货建议 = 按预测30天销量 + BOM倒推物料需求 − 现库存 · 常规单与定制单分开"
             description={<>常规单: 需生产 = 预测 − 现成品库存 − <b>备货在产</b>。「备货在产」= 不挂客户的备货单在产, 到货会进可售库存, 已扣掉; 「客户单在产」= 已卖给下单客户的量, 到货即发走, <b>不抵未来缺口</b>(单独列出仅供参考)。并按需生产倒推全部物料。<br/>定制单: 成品接单才产、无法预备, 但<b>通用料可提前囤</b> → 只列通用料计划(定制专用料随单采购、不预囤)。补货周期 lead 天的物料应在第 (30−lead) 天前下单, 「立即下单」= 现在就该下单。</>} />
      <Card size="small" title="常规单 · 未来 30 天产能缺口"
        extra={<Button icon={<DownloadOutlined />} onClick={exportProducts} disabled={!data?.products?.length}>导出 Excel</Button>}>
        <Table
          size="small" loading={isLoading}
          rowKey={(r: any) => `${r.product_code}`}
          dataSource={data?.products ?? []}
          pagination={{ defaultPageSize: 100, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '产品', width: 240, render: (_: any, r: any) => productCell(r),
              sorter: (a: any, b: any) => String(a.product_name ?? a.product_code).localeCompare(String(b.product_name ?? b.product_code)) },
            { title: '预测 30 天', dataIndex: 'forecast_30d', width: 110,
              sorter: (a: any, b: any) => (a.forecast_30d ?? 0) - (b.forecast_30d ?? 0) },
            { title: '现成品库存', dataIndex: 'in_stock', width: 100 },
            { title: '备货在产', dataIndex: 'in_production_free', width: 100,
              render: (v: number) => (v ?? 0) > 0 ?
                <Tooltip title="备货单在产(不挂客户)、到货会进可售库存 → 已从「需生产」扣掉, 不用重复下单。">
                  <Tag color="blue">{v}</Tag></Tooltip> : <span style={{ color: '#bbb' }}>0</span>,
            },
            { title: '客户单在产', dataIndex: 'in_production_allocated', width: 110,
              render: (v: number) => (v ?? 0) > 0 ?
                <Tooltip title="已卖给下单客户、到货即发走, 不算未来可用库存 → 不抵「需生产」。">
                  <Tag>{v}</Tag></Tooltip> : <span style={{ color: '#bbb' }}>0</span>,
            },
            { title: '需生产', dataIndex: 'need_to_produce', width: 100,
              render: (v: number) => v > 0 ?
                <Tag color="orange">{v}</Tag> : <Tag color="green">充足</Tag>,
            },
          ]}
        />
      </Card>
      <Card size="small" title="常规单 · 按物料应下单时间"
        extra={<Button icon={<DownloadOutlined />} onClick={exportMaterials} disabled={!data?.materials?.length}>导出 Excel</Button>}>
        <Table
          size="small" loading={isLoading}
          rowKey={(r: any) => r.material_code}
          dataSource={data?.materials ?? []}
          pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '物料', dataIndex: 'material_code' },
            { title: '名称', dataIndex: 'material_name' },
            { title: '需求量', dataIndex: 'need_qty', width: 100 },
            { title: '现库存', dataIndex: 'have_qty', width: 80 },
            { title: '缺口', dataIndex: 'missing', width: 90,
              render: (v: number) => v > 0 ? <Tag color="red">{Number(v).toFixed(0)}</Tag> : '-',
            },
            { title: '补货周期', dataIndex: 'lead_time_days', width: 90,
              render: (v: number) => `${v} 天`,
            },
            { title: '建议下单日', dataIndex: 'alert_at', width: 120 },
            { title: '优先级', dataIndex: 'priority', width: 70,
              render: (v: string) => <Tag color={
                v === 'high' ? 'red' : v === 'mid' ? 'orange' : 'default'
              }>{v}</Tag>,
            },
            { title: '现在该下单', dataIndex: 'should_order_now', width: 110,
              render: (v: boolean) => v ?
                <Tag color="red">立即下单</Tag> :
                <Tag color="default">未到时间</Tag>,
            },
          ]}
        />
      </Card>
      <Card size="small" title="定制单 · 通用料备货计划（可提前囤的料）"
        extra={<Button icon={<DownloadOutlined />} onClick={exportCustomMaterials} disabled={!data?.custom_materials?.length}>导出 Excel</Button>}>
        <Alert type="info" showIcon banner style={{ marginBottom: 8 }}
          message="定制单不能预备成品, 但下面这些通用料在多款定制里都会用到, 可提前按量囤货备产。定制专用料不在此列(随单采购)。" />
        <Table
          size="small" loading={isLoading}
          rowKey={(r: any) => r.material_code}
          dataSource={data?.custom_materials ?? []}
          locale={{ emptyText: '近60天无定制单, 或定制单未消耗通用料' }}
          pagination={{ defaultPageSize: 30, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
          columns={[
            { title: '物料', dataIndex: 'material_code' },
            { title: '名称', dataIndex: 'material_name' },
            { title: '定制需求量', dataIndex: 'need_qty', width: 110 },
            { title: '现库存', dataIndex: 'have_qty', width: 80 },
            { title: '缺口', dataIndex: 'missing', width: 90,
              render: (v: number) => v > 0 ? <Tag color="red">{Number(v).toFixed(0)}</Tag> : '-' },
            { title: '补货周期', dataIndex: 'lead_time_days', width: 90, render: (v: number) => `${v} 天` },
            { title: '建议下单日', dataIndex: 'alert_at', width: 120 },
            { title: '现在该下单', dataIndex: 'should_order_now', width: 110,
              render: (v: boolean) => v ? <Tag color="red">立即下单</Tag> : <Tag color="default">未到时间</Tag> },
          ]}
        />
      </Card>
      {data?.semi_finished_enabled && (
        <Card size="small" title="半成品 / 白坯备货计划（池化归集）">
          <Alert type="info" showIcon style={{ marginBottom: 8 }}
                 message="把共享同一白坯的成品预测合并算备货量(池化, 波动比分开囤小)。给产品打标「可做白坯」并设同一白坯分组后, 这里出计划。" />
          <Table
            size="small" loading={isLoading}
            rowKey={(r: any) => r.semi_group}
            dataSource={data?.semi_finished ?? []}
            locale={{ emptyText: '还没有打标「可做白坯」的产品(在产品总表给产品设 semi_group 后出计划)' }}
            pagination={{ defaultPageSize: 20, showSizeChanger: true }}
            columns={[
              { title: '白坯分组', dataIndex: 'semi_group', width: 160 },
              { title: '归集产品数', width: 110, render: (_: any, r: any) => (r.members?.length ?? 0) + ' 款' },
              { title: '池化预测(30天)', dataIndex: 'pooled_forecast', width: 130 },
              { title: '现有白坯', dataIndex: 'on_hand', width: 110,
                render: (v: number, r: any) => <InputNumber size="small" min={0} defaultValue={v} style={{ width: 88 }}
                  onBlur={(e) => saveSemi(r.semi_group, 'on_hand_qty', Number((e.target as HTMLInputElement).value) || 0)} /> },
              { title: '在产白坯', dataIndex: 'in_production', width: 110,
                render: (v: number, r: any) => <InputNumber size="small" min={0} defaultValue={v} style={{ width: 88 }}
                  onBlur={(e) => saveSemi(r.semi_group, 'in_production_qty', Number((e.target as HTMLInputElement).value) || 0)} /> },
              { title: '建议备白坯', dataIndex: 'recommend_semi', width: 120,
                render: (v: number) => v > 0 ? <Tag color="purple">{v}</Tag> : <Tag color="green">充足</Tag> },
            ]}
          />
        </Card>
      )}
    </Space>
  );
}

function SlowMovingTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['slow-moving'], queryFn: () => fetchSlowMoving(),
  });
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert
        type="warning" showIcon
        message={`阈值: ${data?.thresholds?.long_no_sale_days ?? 60} 天未售 = 长期滞销; > ${data?.thresholds?.overstock_ratio ?? 3} 倍预测 = 超大库存`}
      />
      <Row gutter={16}>
        <Col xs={24} sm={12}>
          <Card size="small" title="长期未售 (物料)">
            <Table size="small" loading={isLoading}
                   rowKey="material_code"
                   dataSource={data?.long_idle ?? []}
                   pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
                   columns={[
                     { title: '物料', dataIndex: 'material_code', width: 110 },
                     { title: '名称', dataIndex: 'material_name', ellipsis: true },
                     { title: '库存', dataIndex: 'physical_qty', width: 80 },
                     { title: '最后出货', dataIndex: 'last_outbound_at' },
                     { title: '滞销天数', dataIndex: 'days_since', width: 90,
                       render: (v: number) => <Tag color="orange">{v} 天</Tag> },
                   ]} />
          </Card>
        </Col>
        <Col xs={24} sm={12}>
          <Card size="small" title="超大库存 (成品)">
            <Table size="small" loading={isLoading}
                   rowKey={(r: any) => `${r.product_code}_${r.sku}`}
                   dataSource={data?.overstock ?? []}
                   pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }}
                   columns={[
                     { title: '产品', width: 220, render: (_: any, r: any) => productCell(r) },
                     { title: 'SKU', dataIndex: 'sku' },
                     { title: '库存', dataIndex: 'physical_qty', width: 80 },
                     { title: '预测 30 天', dataIndex: 'forecast_30d', width: 100 },
                     { title: '倍数', dataIndex: 'ratio', width: 80,
                       render: (v: number) => <Tag color="red">{v} ×</Tag> },
                   ]} />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
