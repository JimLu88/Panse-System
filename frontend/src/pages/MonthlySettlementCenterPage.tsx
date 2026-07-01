/**
 * 月度对账中心 (用户 2026-06-27, 方向三)。
 *
 * 统一所有「月结」供应商对账: 配件月结(五金/电力轨道/岩板/玻璃) + 打包月结 + 运费月结。
 * 每域逐月: 预估应付 | 实际账单 | 差异 | 差异%。一键导出全部月结账单(每月×每种)。
 *
 * 口径红线: 这是【供应商应付(AP)核对】, 只为"这个月供应商收我多少、对不对", 不参与产品成本分摊
 * (打包/运费早已计入每单 physical_cost)。全部按发货日期(ship_date)对账。
 */
import { useState } from 'react';
import { Alert, Button, Card, Collapse, Dropdown, Empty, Space, Table, Tag, Typography, message } from 'antd';
import { DownloadOutlined, PrinterOutlined, ReloadOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import {
  downloadMonthlySettlementAll,
  downloadPackingChecklistXlsx,
  fetchMonthlySettlementCenter,
  fetchPackingChecklist,
  type PackingChecklist,
  type SettlementGroup,
  type SettlementRow,
} from '../api/monthlySettlement';

const yuan = (v: number | null | undefined) => (v == null ? '—' : `¥${Math.round(v).toLocaleString()}`);
// 差异 = 实际 − 预估。正(实际>预估)= 预估偏低 → 红; 负 = 预估偏高 → 绿。
const varColor = (v: number | null | undefined) =>
  v == null ? '#999' : Math.abs(v) < 1 ? '#999' : v > 0 ? '#cf1322' : '#389e0d';

const escHtml = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m] as string));

// 打包导清单打印(A4横向扁平表格)。订单号在 HTML <td> 里就是文本, 无科学计数法问题。
function openPackingChecklistPrint(d: PackingChecklist) {
  const win = window.open('', '_blank', 'width=980,height=860');
  if (!win) { message.warning('浏览器拦截了打印窗口, 请允许弹窗后重试'); return; }
  const y2 = (v: number | null | undefined) => (v == null ? '—' : '¥' + Number(v).toFixed(2));
  const rows = d.orders.map((o) => `<tr>
    <td class="code">${escHtml(o.order_no)}</td><td>${escHtml(o.order_date ?? '')}</td><td>${escHtml(o.ship_date ?? '')}</td>
    <td>${escHtml(o.customer_name ?? '')}</td><td>${escHtml(o.product_name ?? '')}</td><td>${escHtml(o.sku ?? '')}</td>
    <td class="num">${y2(o.actual_packing)}</td></tr>`).join('');
  const title = `打包对账清单 · ${d.year_month} 发货`;
  win.document.write(`<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>${escHtml(title)}</title>
    <style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,"Microsoft YaHei",sans-serif;color:#222;padding:10mm}
    h1{font-size:16px;margin-bottom:4px}.sub{color:#666;font-size:12px;margin-bottom:10px}
    table{width:100%;border-collapse:collapse;font-size:12px}th,td{border:1px solid #bbb;padding:4px 6px;text-align:left}
    th{background:#f5f5f5}.num{text-align:right;font-variant-numeric:tabular-nums}.code{font-family:monospace;font-size:10px;word-break:break-all}
    .tot td{background:#f5f5f5;font-weight:700}@page{size:A4 landscape;margin:10mm}</style></head>
    <body><h1>${escHtml(title)}</h1>
    <div class="sub">共 ${d.order_count} 单 · 按发货日期(ship_date) · 实际打包费合计 ¥${Number(d.total_actual_packing).toFixed(2)} &nbsp;|&nbsp; 空=该单打包账单未配到</div>
    <table><thead><tr><th>订单号</th><th>下单日期</th><th>发货日</th><th>客户</th><th>产品</th><th>SKU(含尺寸)</th><th>实际打包费</th></tr></thead>
    <tbody>${rows}<tr class="tot"><td colspan="6" class="num">合计</td><td class="num">${y2(d.total_actual_packing)}</td></tr></tbody></table>
    </body></html>`);
  win.document.close();
  win.focus();
  setTimeout(() => win.print(), 400);
}

function GroupTable({ group, domainKey }: { group: SettlementGroup; domainKey: string }) {
  const onChecklist = async (period: string, fmt: 'print' | 'xlsx') => {
    try {
      if (fmt === 'xlsx') { await downloadPackingChecklistXlsx(period); return; }
      const d = await fetchPackingChecklist(period);
      if (!d.order_count) { message.info(`${period} 没有已发货订单`); return; }
      openPackingChecklistPrint(d);
    } catch (e: any) {
      message.error(`导出失败: ${e?.response?.data?.detail || e?.message || e}`);
    }
  };
  const columns = [
    { title: '月份', dataIndex: 'period', key: 'period', width: 100 },
    {
      title: '预估应付', dataIndex: 'estimate', key: 'estimate', align: 'right' as const,
      render: (v: number) => yuan(v),
    },
    {
      title: '实际账单', dataIndex: 'actual', key: 'actual', align: 'right' as const,
      render: (v: number | null) =>
        v == null ? <Tag color="default">未录</Tag> : <b>{yuan(v)}</b>,
    },
    {
      title: '差异', dataIndex: 'variance', key: 'variance', align: 'right' as const,
      render: (v: number | null) =>
        v == null ? '—' : <span style={{ color: varColor(v) }}>{v > 0 ? '+' : ''}{yuan(v)}</span>,
    },
    {
      title: '差异%', dataIndex: 'variance_pct', key: 'variance_pct', align: 'right' as const,
      render: (v: number | null) =>
        v == null ? '—' : <span style={{ color: varColor(v) }}>{v > 0 ? '+' : ''}{v}%</span>,
    },
    {
      title: '发货单数', dataIndex: 'order_count', key: 'order_count', align: 'right' as const,
      render: (v: number | null) => v ?? '—',
    },
    ...(domainKey === 'packing' ? [{
      title: '导清单', key: 'checklist', width: 116,
      render: (_: unknown, r: SettlementRow) => (
        <Dropdown menu={{ items: [
          { key: 'print', icon: <PrinterOutlined />, label: '打印清单 (A4)' },
          { key: 'xlsx', icon: <DownloadOutlined />, label: '下载 Excel (.xlsx)' },
        ], onClick: ({ key }) => onChecklist(r.period, key as 'print' | 'xlsx') }}>
          <Button size="small" icon={<PrinterOutlined />} disabled={!r.order_count}>导清单</Button>
        </Dropdown>
      ),
    }] : []),
  ];
  return (
    <Table<SettlementRow>
      size="small"
      rowKey="period"
      columns={columns}
      dataSource={group.rows}
      pagination={false}
      locale={{ emptyText: '暂无数据' }}
      summary={() => (
        <Table.Summary.Row>
          <Table.Summary.Cell index={0}><b>合计</b></Table.Summary.Cell>
          <Table.Summary.Cell index={1} align="right"><b>{yuan(group.total_estimate)}</b></Table.Summary.Cell>
          <Table.Summary.Cell index={2} align="right"><b>{yuan(group.total_actual)}</b></Table.Summary.Cell>
          <Table.Summary.Cell index={3} align="right">
            <b style={{ color: varColor(group.total_variance) }}>{yuan(group.total_variance)}</b>
          </Table.Summary.Cell>
          <Table.Summary.Cell index={4} align="right">
            <b style={{ color: varColor(group.total_variance_pct) }}>
              {group.total_variance_pct == null ? '—' : `${group.total_variance_pct}%`}
            </b>
          </Table.Summary.Cell>
          <Table.Summary.Cell index={5} />
          {domainKey === 'packing' && <Table.Summary.Cell index={6} />}
        </Table.Summary.Row>
      )}
    />
  );
}

export default function MonthlySettlementCenterPage() {
  const [exporting, setExporting] = useState(false);
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['monthly-settlement-center'],
    queryFn: fetchMonthlySettlementCenter,
  });

  const onExport = async () => {
    setExporting(true);
    try {
      await downloadMonthlySettlementAll();
      message.success('已导出全部月结账单');
    } catch (e: any) {
      message.error(`导出失败: ${e?.response?.data?.detail || e?.message || e}`);
    } finally {
      setExporting(false);
    }
  };

  const domains = data?.domains ?? [];

  return (
    <div style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 12 }} align="start">
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>月结对账中心</Typography.Title>
          <Typography.Text type="secondary">配件 · 打包 · 运费 —— 按发货月统一对账</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>刷新</Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={onExport} loading={exporting}>
            一键导出全部月结账单
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="供应商应付(AP)核对 · 不计入产品成本"
        description={
          <span>
            这里只核对「这个月各供应商收我多少、和我们按发货量预估的应付对不对」。打包/运费早已计入每单成本
            (physical_cost), <b>此处不会再加一遍</b>。预估=当月发货单的应付基准, 实际=供应商账单
            (配件→工厂月度填总额; 打包→OCR手写账单; 运费→物流账单逐单按月)。差异为正=实际比预估高。
          </span>
        }
      />

      {isLoading ? (
        <Card loading />
      ) : domains.length === 0 ? (
        <Empty description="暂无月结数据" />
      ) : (
        <Collapse
          defaultActiveKey={domains.map((d) => d.key)}
          items={domains.map((dom) => ({
            key: dom.key,
            label: (
              <Space>
                <b>{dom.label}</b>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{dom.settle_hint}</Typography.Text>
              </Space>
            ),
            children:
              dom.groups.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该域暂无月结分类" />
              ) : dom.groups.length === 1 ? (
                <GroupTable group={dom.groups[0]} domainKey={dom.key} />
              ) : (
                <Collapse
                  ghost
                  defaultActiveKey={dom.groups.map((g) => g.key)}
                  items={dom.groups.map((g) => ({
                    key: g.key,
                    label: (
                      <Space>
                        <Tag color="blue">{g.label}</Tag>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          预估 {yuan(g.total_estimate)} · 实际 {yuan(g.total_actual)} ·{' '}
                          <span style={{ color: varColor(g.total_variance) }}>差异 {yuan(g.total_variance)}</span>
                        </Typography.Text>
                      </Space>
                    ),
                    children: <GroupTable group={g} domainKey={dom.key} />,
                  }))}
                />
              ),
          }))}
        />
      )}
    </div>
  );
}
