// ChatBI 问数抽屉 (Plan4 v2 §7)。顶栏"问数"入口, 不打断当前页。
// 四态徽标: ✅模板 / 🟡半生成 / ⚠AI直出 / ⛔拒答·ℹ️指向报表页。
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Drawer, Input, Button, Tag, Table, Collapse, Space, List, Empty, Spin,
  Typography, message as antdMessage,
} from 'antd';
import { LikeOutlined, DislikeOutlined, SendOutlined, BulbOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { api } from '../../api/base';

const { Text, Paragraph } = Typography;

interface Column { name: string; label?: string; kind?: string; }
interface AskResp {
  query_id?: number | null;
  route: string;
  badge: string;
  template_key?: string | null;
  columns: Column[];
  rows: unknown[][];
  chart: { type: string; [k: string]: unknown };
  sql?: string | null;
  caliber_notes: string[];
  message?: string;
  data_as_of?: string | null;
  suggestions?: string[];
}

const BADGE: Record<string, { color: string; text: string }> = {
  verified: { color: 'green', text: '✅ 模板·口径已审' },
  semi: { color: 'gold', text: '🟡 半生成·口径字典拼装' },
  generated: { color: 'orange', text: '⚠ AI直出·口径未审' },
  refused: { color: 'default', text: '⛔ 无法作答' },
  pointer: { color: 'blue', text: 'ℹ️ 请到对应报表页' },
};

function buildChartOption(resp: AskResp): Record<string, unknown> | null {
  const { chart, columns, rows } = resp;
  if (!chart || !rows?.length) return null;
  const idx = (name: string) => columns.findIndex((c) => c.name === name);
  const col = (name: string, i: number) => rows.map((r) => r[idx(name) >= 0 ? idx(name) : i]);
  try {
    if (chart.type === 'line') {
      const x = col(chart.x as string, 0);
      const ys = (chart.y as string[]) || [];
      return {
        tooltip: { trigger: 'axis' },
        legend: ys.length > 1 ? { data: ys } : undefined,
        grid: { left: 48, right: 16, top: 24, bottom: 40 },
        xAxis: { type: 'category', data: x },
        yAxis: { type: 'value' },
        series: ys.map((y) => ({ name: y, type: 'line', smooth: true, data: col(y, 1) })),
      };
    }
    if (chart.type === 'bar') {
      const x = col(chart.x as string, 0).map(String);
      const y = col(chart.y as string, 1);
      return {
        tooltip: { trigger: 'axis' },
        grid: { left: 100, right: 24, top: 16, bottom: 32 },
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: x, inverse: true },
        series: [{ type: 'bar', data: y }],
      };
    }
    if (chart.type === 'pie') {
      const names = col(chart.name as string, 0);
      const vals = col(chart.value as string, 1);
      return {
        tooltip: { trigger: 'item' },
        series: [{ type: 'pie', radius: ['40%', '70%'],
          data: names.map((n, i) => ({ name: String(n), value: vals[i] })) }],
      };
    }
    if (chart.type === 'scatter') {
      return {
        tooltip: { trigger: 'item' },
        xAxis: {}, yAxis: {},
        series: [{ type: 'scatter', data: rows.map((r) => [r[0], r[1]]) }],
      };
    }
  } catch {
    return null;
  }
  return null;
}

export default function ChatBiDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [resp, setResp] = useState<AskResp | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [llmOnline, setLlmOnline] = useState<boolean | null>(null);
  const [history, setHistory] = useState<{ id: number; question: string; route: string }[]>([]);

  const loadMeta = useCallback(async () => {
    try {
      const s = await api.get('/api/chatbi/suggestions');
      setSuggestions(s.data.suggestions || []);
      setLlmOnline(s.data.llm_online);
    } catch { /* ignore */ }
    try {
      const h = await api.get('/api/chatbi/history?limit=15');
      setHistory(h.data.items || []);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { if (open) loadMeta(); }, [open, loadMeta]);

  const ask = useCallback(async (q: string) => {
    const query = (q || '').trim();
    if (!query) return;
    setLoading(true);
    setResp(null);
    try {
      const r = await api.post('/api/chatbi/ask', { question: query }, { timeout: 130000 });
      setResp(r.data);
      loadMeta();
    } catch {
      antdMessage.error('问数失败, 请重试');
    } finally {
      setLoading(false);
    }
  }, [loadMeta]);

  const feedback = useCallback(async (fb: 'up' | 'down') => {
    if (!resp?.query_id) return;
    try {
      await api.post('/api/chatbi/feedback', { query_id: resp.query_id, feedback: fb });
      antdMessage.success('已记录反馈, 谢谢');
    } catch { antdMessage.error('反馈失败'); }
  }, [resp]);

  const chartOption = useMemo(() => (resp ? buildChartOption(resp) : null), [resp]);

  const tableColumns = useMemo(() => (resp?.columns || []).map((c, i) => ({
    title: c.label || c.name, dataIndex: i, key: c.name,
    render: (v: unknown) => (typeof v === 'number' ? v.toLocaleString() : String(v ?? '')),
  })), [resp]);
  const tableData = useMemo(() => (resp?.rows || []).map((r, i) => {
    const o: Record<string, unknown> = { key: i };
    r.forEach((v, j) => { o[j] = v; });
    return o;
  }), [resp]);

  const badge = resp ? (BADGE[resp.badge] || BADGE.refused) : null;

  return (
    <Drawer
      title="问数 (ChatBI)"
      placement="right"
      open={open}
      onClose={onClose}
      width={Math.min(760, typeof window !== 'undefined' ? window.innerWidth : 760)}
      styles={{ body: { paddingTop: 12 } }}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Input.Search
          placeholder="用大白话问：本月净利润 / 产品毛利率排行 / 退款率趋势…"
          enterButton={<><SendOutlined /> 问</>}
          size="large"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onSearch={ask}
          loading={loading}
        />

        {llmOnline === false && (
          <Tag color="warning" icon={<BulbOutlined />}>AI 引擎离线 — 模板问数仍可用</Tag>
        )}

        {suggestions.length > 0 && !resp && (
          <Space wrap size={[8, 8]}>
            {suggestions.map((s) => (
              <Tag key={s} style={{ cursor: 'pointer' }} onClick={() => { setQuestion(s); ask(s); }}>{s}</Tag>
            ))}
          </Space>
        )}

        {loading && <div style={{ textAlign: 'center', padding: 24 }}><Spin tip="思考中…" /></div>}

        {resp && (
          <div>
            <Space wrap style={{ marginBottom: 8 }}>
              {badge && <Tag color={badge.color}>{badge.text}</Tag>}
              {resp.data_as_of && <Text type="secondary" style={{ fontSize: 12 }}>数据截至 {resp.data_as_of.slice(0, 16).replace('T', ' ')}</Text>}
            </Space>

            {resp.message && <Paragraph type={resp.badge === 'pointer' ? undefined : 'secondary'}>{resp.message}</Paragraph>}

            {chartOption && <ReactECharts option={chartOption} style={{ height: 300 }} notMerge />}

            {resp.rows?.length > 0 && (
              <Table size="small" columns={tableColumns} dataSource={tableData}
                     pagination={resp.rows.length > 20 ? { pageSize: 20 } : false}
                     scroll={{ x: true }} style={{ marginTop: 8 }} />
            )}

            {resp.rows?.length === 0 && resp.badge !== 'pointer' && resp.badge !== 'refused' && (
              <Empty description="该区间没有数据" />
            )}

            {(resp.caliber_notes?.length > 0 || resp.sql) && (
              <Collapse
                size="small"
                style={{ marginTop: 12 }}
                items={[
                  ...(resp.caliber_notes?.length ? [{
                    key: 'caliber', label: '口径说明',
                    children: <List size="small" dataSource={resp.caliber_notes}
                                    renderItem={(n) => <List.Item>{n}</List.Item>} />,
                  }] : []),
                  ...(resp.sql ? [{
                    key: 'sql', label: '查看 SQL',
                    children: <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: 0 }}>{resp.sql}</pre>,
                  }] : []),
                ]}
              />
            )}

            {resp.query_id && resp.badge !== 'refused' && resp.badge !== 'pointer' && (
              <Space style={{ marginTop: 12 }}>
                <Button size="small" icon={<LikeOutlined />} onClick={() => feedback('up')}>有用</Button>
                <Button size="small" icon={<DislikeOutlined />} onClick={() => feedback('down')}>不对</Button>
              </Space>
            )}
          </div>
        )}

        {history.length > 0 && (
          <Collapse size="small" items={[{
            key: 'h', label: `历史问答 (${history.length})`,
            children: <List size="small" dataSource={history}
              renderItem={(h) => (
                <List.Item style={{ cursor: 'pointer' }} onClick={() => { setQuestion(h.question); ask(h.question); }}>
                  <Text ellipsis style={{ maxWidth: '100%' }}>{h.question}</Text>
                </List.Item>
              )} />,
          }]} />
        )}
      </Space>
    </Drawer>
  );
}
