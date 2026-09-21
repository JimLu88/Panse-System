import { useMemo, useState } from 'react';
import { Alert, Button, Card, Collapse, Drawer, Empty, Input, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/base';
import './BusinessRelationships.css';
import BusinessRelationshipReview, { downloadReview } from './BusinessRelationshipReview';

type Source = { path: string; anchor: string; line: number | null; state: string };
type Node = { id: string; label: string; domain: string; field: string; note: string; sources: Source[]; source_current: boolean };
type Edge = { id: string; from: string; to: string; action: string; condition: string; check: string; kind: string; evidence: string; sources: Source[]; source_current: boolean };
type Data = { version: string; reviewed_at: string | null; notice: string; domains: { id: string; label: string; owner: string }[]; nodes: Node[]; edges: Edge[]; flows: { id: string; name: string; steps: string[] }[]; source_states: Record<string, string>; validation_errors: string[]; coverage: { model_count: number; field_count: number; file_count: number; referenced_file_count: number; unmapped_files: string[]; note: string; errors: unknown[]; models: { model: string; table: string; path: string; fields: string[]; detail_status: string }[] } };
const evidenceLabel: Record<string, string> = { code: '已核代码', review: '待核分支', gap: '联动缺口' };
const kindLabel: Record<string, string> = { data: '条件关联', protect: '禁止自动覆盖', gap: '需要补核', boundary: '权限/系统边界' };
const stateLabel: Record<string, string> = { matching: '源码指纹一致', changed: '源码已变，需复核', missing: '来源文件缺失', unreviewed: '尚未锁定审查', anchor_missing: '原程序位置已变化' };

function Sources({ items }: { items: Source[] }) {
  return <ul className="br-sources">{items.map((s, i) => <li key={`${s.path}-${i}`}><code>{s.path}{s.line ? `:${s.line}` : ''}</code><br /><Tag color={s.state === 'matching' ? 'blue' : 'orange'}>{stateLabel[s.state] || s.state}</Tag></li>)}</ul>;
}

function reach(data: Data, root: string, direction: 'from' | 'to', depth: number) {
  const distances = new Map([[root, 0]]), edges = new Map<string, Edge>();
  const queue = [root];
  while (queue.length) {
    const id = queue.shift()!, distance = distances.get(id)!;
    if (distance >= depth) continue;
    data.edges.filter(e => e[direction] === id).forEach(e => {
      edges.set(e.id, e);
      const next = e[direction === 'from' ? 'to' : 'from'];
      if (!distances.has(next)) { distances.set(next, distance + 1); queue.push(next); }
    });
  }
  const truncated = data.edges.some(e => distances.get(e[direction]) === depth && !distances.has(e[direction === 'from' ? 'to' : 'from']));
  return { distances, edges: [...edges.values()], truncated };
}

function ImpactGraph({ data, root, depth, select }: { data: Data; root: string; depth: number; select: (n: Node) => void }) {
  const up = reach(data, root, 'to', depth), down = reach(data, root, 'from', depth);
  const chosen = data.nodes.find(n => n.id === root)!;
  const columns = [data.nodes.filter(n => n.id !== root && up.distances.has(n.id)), [chosen], data.nodes.filter(n => n.id !== root && down.distances.has(n.id))];
  // Cycles are listed in both sides only in the textual path details, not duplicated in the graph.
  columns[2] = columns[2].filter(n => !columns[0].some(a => a.id === n.id));
  const positions = new Map<string, { x: number; y: number }>();
  const height = Math.max(230, Math.max(...columns.map(c => c.length)) * 78 + 65);
  columns.forEach((nodes, c) => nodes.forEach((n, r) => positions.set(n.id, { x: 20 + c * 305, y: c === 1 ? height / 2 - 24 : 54 + r * 78 })));
  const edges = [...new Map([...up.edges, ...down.edges].map(e => [e.id, e])).values()];
  return <>
    {(up.truncated || down.truncated) && <Alert type="info" message="还有更远的关联尚未展开；当前图不能作为全部影响清单。可提高展开层数或按下方明细继续核对。" />}
    <Typography.Text type="secondary">图内可横向、纵向滚动；点击任一节点查看条件和程序依据。</Typography.Text>
    <div className="br-graph" aria-label="字段上下游影响图">
      <svg viewBox={`0 0 920 ${height}`} width="920" height={height} role="img" aria-label={`以${chosen.label}为中心的影响检查图`}>
        <defs><marker id="br-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#8494ad" /></marker></defs>
        {['来源 / 上游检查', '本次关注字段', '下游 / 需要检查'].map((s, i) => <text key={s} x={20 + i * 305} y={25} fill="#526075" fontSize="14">{s}</text>)}
        {edges.map(e => {
          const a = positions.get(e.from), b = positions.get(e.to); if (!a || !b) return null;
          const forward = a.x < b.x, x1 = a.x + (forward ? 266 : 0), x2 = b.x + (forward ? 0 : 266);
          return <path key={e.id} d={`M${x1},${a.y + 25} C${(x1 + x2) / 2},${a.y + 25} ${(x1 + x2) / 2},${b.y + 25} ${x2},${b.y + 25}`} fill="none" stroke={e.kind === 'protect' ? '#c47a32' : e.evidence === 'gap' ? '#c94b61' : '#adb8ca'} strokeDasharray={e.evidence !== 'code' || e.kind === 'protect' ? '5 4' : undefined} markerEnd="url(#br-arrow)"><title>{e.action}：{e.condition}</title></path>;
        })}
        {columns.flat().map(n => { const p = positions.get(n.id)!; return <g key={n.id} transform={`translate(${p.x} ${p.y})`} role="button" tabIndex={0} aria-label={`查看${n.label}`} onClick={() => select(n)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(n); } }} style={{ cursor: 'pointer' }}>
          <rect width="266" height="53" rx="10" fill={n.id === root ? '#e8f1ff' : '#fff'} stroke={n.id === root ? '#3477d5' : '#ccd5e2'} />
          <text x="12" y="22" fontSize="14" fill="#182d48">{n.label}</text><text x="12" y="41" fontSize="11" fill="#65748b">{data.domains.find(d => d.id === n.domain)?.label} · {n.source_current ? '结构已登记' : '来源需复核'}</text>
        </g>; })}
      </svg>
    </div>
    <Typography.Paragraph type="secondary">实线：代码中已核关系；虚线：待核分支、联动缺口或禁止覆盖。箭头表示检查方向，不代表自动修改许可。所有条件见下表。</Typography.Paragraph>
    <Table size="small" rowKey="id" dataSource={edges} scroll={{ x: 900 }} pagination={{ pageSize: 12 }} columns={[
      { title: '关系', render: (_, e) => <><b>{data.nodes.find(n => n.id === e.from)?.label}</b> → {data.nodes.find(n => n.id === e.to)?.label}<div>{e.action}</div></> },
      { title: '条件 / 必须验证', render: (_, e) => <>{e.condition}<div className="br-check">检查：{e.check}</div></> },
      { title: '性质与证据', render: (_, e) => <><Tag color={e.kind === 'protect' ? 'orange' : e.evidence === 'gap' ? 'red' : 'blue'}>{kindLabel[e.kind]}</Tag><Tag>{evidenceLabel[e.evidence]}</Tag>{!e.source_current && <Tag color="orange">来源需复核</Tag>}</> },
      { title: '程序依据', render: (_, e) => <Sources items={e.sources} /> },
    ]} />
  </>;
}

export default function BusinessRelationships() {
  const query = useQuery({ queryKey: ['business-relationships'], queryFn: () => api.get<Data>('/api/admin/business-relationships').then(r => r.data), staleTime: 60000, retry: false });
  const [tab, setTab] = useState('map'), [search, setSearch] = useState(''), [root, setRoot] = useState('quantity.physical'), [depth, setDepth] = useState(2), [flowId, setFlowId] = useState('order');
  const [detail, setDetail] = useState<Node | null>(null);
  const [fieldChoice, setFieldChoice] = useState('OrderDetail.qty'), [scanField, setScanField] = useState('');
  const refs = useQuery({ queryKey: ['business-field-references', scanField], enabled: !!scanField, retry: false,
    queryFn: () => api.get<{ total: number; truncated: boolean; notice: string; candidates: { path: string; line: number; kind: string; receiver: string }[] }>('/api/admin/business-relationships/field', { params: { model: scanField.split('.')[0], field: scanField.split('.')[1] } }).then(r => r.data) });
  const data = query.data;
  const visible = useMemo(() => data?.nodes.filter(n => `${n.label} ${n.field} ${n.note}`.toLowerCase().includes(search.toLowerCase())) || [], [data, search]);
  if (query.isPending) return <Spin tip="正在读取业务关系目录"><div style={{ height: 180 }} /></Spin>;
  if (query.isError || !data) return <Alert type="error" showIcon message="关系目录暂时无法读取（可能是权限或连接问题）" description="没有触发任何业务操作。" action={<Button onClick={() => query.refetch()}>重新读取</Button>} />;
  const drift = Object.values(data.source_states).filter(s => s !== 'matching').length;
  if (!data.nodes.length || !data.flows.length || data.validation_errors.length) return <Alert type="error" message="关系目录不完整，暂不展示可能误导的关系图" description="请修复目录对象、流程或引用；没有执行任何业务操作。" />;
  const flow = data.flows.find(f => f.id === flowId) || data.flows[0];
  const selectedRoot = data.nodes.some(n => n.id === root) ? root : data.nodes[0].id;
  const owners = [...new Set(flow.steps.map(id => data.domains.find(d => d.id === data.nodes.find(n => n.id === id)?.domain)?.owner || '待核实'))];
  const focus = (n: Node) => { setRoot(n.id); setTab('impact'); };
  return <Space direction="vertical" size="middle" style={{ width: '100%' }} className="business-relationships">
    <div className="br-header"><div><Typography.Title level={3} style={{ margin: 0 }}>关系流程</Typography.Title><Typography.Text type="secondary">先看业务，再检查改动会影响哪里</Typography.Text></div><Space wrap><Button onClick={() => downloadReview(data, '业务关系目录.json')}>导出关系目录</Button><Button onClick={() => query.refetch()}>刷新关系核查</Button></Space></div>
    <Alert type="info" showIcon message="只读业务关系目录，不是订单运行看板" description="这里展示来源、条件和检查要求；不会报名、发单、修改库存或财务，也不会把“已发图”当作“已经发齐”。" />
    <Space wrap><Tag>{data.domains.length} 个业务域</Tag><Tag>{data.nodes.length} 个重点对象/字段</Tag><Tag>{data.edges.length} 条审查关系</Tag><Tag>版本 {data.version}</Tag><Tag>代码目录：{data.coverage.model_count} 模型 / {data.coverage.field_count} 字段</Tag></Space>
    {(drift > 0 || data.validation_errors.length > 0 || data.coverage.errors.length > 0) && <Alert type="warning" showIcon message={`${drift} 处源码需复核；目录错误 ${data.validation_errors.length}，扫描异常 ${data.coverage.errors.length}`} description="不可把过期或缺失关系当成已验证。" />}
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'changes', label: '改动影响核查', children: <BusinessRelationshipReview /> },
      { key: 'map', label: '业务地图', children: <><Input.Search aria-label="搜索业务或字段" placeholder="搜索业务、字段或规则，例如：数量、退款、成本" value={search} onChange={e => setSearch(e.target.value)} allowClear style={{ maxWidth: 540, marginBottom: 16 }} />{!visible.length && <Empty description="没有匹配关系；不代表该业务没有影响" />}<div className="br-map">{data.domains.map(d => { const nodes = visible.filter(n => n.domain === d.id); return nodes.length ? <Card size="small" title={d.label} extra={<Tag>{d.owner}</Tag>} key={d.id}><div className="br-node-list">{nodes.map(n => <button key={n.id} onClick={() => focus(n)} className="br-node-button"><span>{n.label}</span><small>{n.field}</small></button>)}</div></Card> : null; })}</div></> },
      { key: 'flow', label: '流程泳道', children: <><Select aria-label="选择流程" value={flowId} onChange={setFlowId} options={data.flows.map(f => ({ value: f.id, label: f.name }))} style={{ width: '100%', maxWidth: 600 }} /><Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>这是业务理解顺序，不是强制执行串行。无需数量确认的普通单按原流程直达制单；并行输入及分支请点节点看关系。横向滚动查看完整泳道。</Typography.Paragraph><div className="br-lanes"><div style={{ display: 'grid', gridTemplateColumns: `110px repeat(${flow.steps.length}, 170px)`, gap: 8 }}><strong>责任方 / 阅读顺序</strong>{flow.steps.map((s, i) => <div className="br-step" key={s}>{i + 1} {i < flow.steps.length - 1 ? '→' : ''}</div>)}{owners.map(owner => <div style={{ display: 'contents' }} key={owner}><strong className="br-lane-name">{owner}</strong>{flow.steps.map(id => { const n = data.nodes.find(n => n.id === id)!; const match = data.domains.find(d => d.id === n.domain)?.owner === owner; return <div key={id} className="br-lane-cell">{match && <button className="br-node-button" onClick={() => setDetail(n)}>{n.label}<small>{n.source_current ? '查看条件与依据' : '来源需复核'}</small></button>}</div>; })}</div>)}</div></div></> },
      { key: 'impact', label: '字段影响', children: <><Space wrap style={{ marginBottom: 16 }}><Select aria-label="选择关注字段" showSearch optionFilterProp="label" value={selectedRoot} onChange={setRoot} options={data.nodes.map(n => ({ value: n.id, label: `${n.label} · ${n.field}` }))} style={{ width: 330, maxWidth: '100%' }} /><Select aria-label="展开层数" value={depth} onChange={setDepth} options={[1, 2, 3, 6, 12].map(n => ({ value: n, label: `展开 ${n} 层` }))} /></Space><ImpactGraph data={data} root={selectedRoot} depth={depth} select={setDetail} /></> },
      { key: 'coverage', label: '遗漏与过期检查', children: <Space direction="vertical" style={{ width: '100%' }}><Alert type="warning" showIcon message="这是待补全清单，不是全覆盖合格证" description={data.coverage.note} /><Typography.Paragraph>扫描 {data.coverage.file_count} 个程序文件，其中 {data.coverage.referenced_file_count} 个被重点关系引用；其余 {data.coverage.unmapped_files.length} 个尚未登记详细语义。以下模型字段目录支持查漏，不自动生成猜测关系。</Typography.Paragraph>
        <Card size="small" title="任意模型字段的代码候选引用"><Space wrap><Select aria-label="选择要核查的模型字段" showSearch optionFilterProp="label" value={fieldChoice} onChange={setFieldChoice} style={{ width: 320, maxWidth: '100%' }} options={data.coverage.models.flatMap(m => m.fields.map(f => ({ value: `${m.model}.${f}`, label: `${m.model}.${f}` })))} /><Button loading={refs.isFetching} onClick={() => scanField === fieldChoice ? refs.refetch() : setScanField(fieldChoice)}>只读查找字段引用</Button></Space>
          {refs.isError && <Alert type="error" message="字段引用读取失败，不能据此判定没有影响。" />}
          {refs.data && <><Alert type="info" style={{ marginTop: 12 }} message={`找到 ${refs.data.total} 处候选引用${refs.data.truncated ? '，当前只展示前500处' : ''}`} description={refs.data.notice} /><Table size="small" rowKey={r => `${r.path}:${r.line}:${r.kind}`} dataSource={refs.data.candidates} pagination={{ pageSize: 8 }} scroll={{ x: 700 }} columns={[{ title: '程序位置', render: (_, r) => <code>{r.path}:{r.line}</code> }, { title: '候选类型', dataIndex: 'kind' }, { title: '接收对象（仍需核对类型）', dataIndex: 'receiver' }]} /></>}
        </Card><Collapse items={[
        { key: 'sources', label: `源码指纹核查（${drift} 处待复核）`, children: <ul className="br-sources">{Object.entries(data.source_states).map(([path, state]) => <li key={path}><Tag color={state === 'matching' ? 'blue' : 'orange'}>{stateLabel[state]}</Tag><code>{path}</code></li>)}</ul> },
        { key: 'unmapped', label: `未登记关系的文件（${data.coverage.unmapped_files.length}）`, children: <ul className="br-sources">{data.coverage.unmapped_files.map(p => <li key={p}><code>{p}</code></li>)}</ul> },
        { key: 'models', label: `完整模型与字段索引（${data.coverage.model_count}）`, children: <Table size="small" rowKey={m => `${m.path}:${m.model}`} dataSource={data.coverage.models} pagination={{ pageSize: 10 }} scroll={{ x: 720 }} columns={[{ title: '对象/表', render: (_, m) => <>{m.model}<br />{m.table}</> }, { title: '字段目录（非逐字段已验证）', render: (_, m) => <div className="br-wrap">{m.fields.join(' · ')}</div> }, { title: '程序', dataIndex: 'path' }]} /> },
      ]} /></Space> },
    ]} />
    {tab === 'flow' && <Card size="small" title="流程条件、分支及跨流程影响"><Table size="small" rowKey="id" scroll={{ x: 760 }} pagination={{ pageSize: 10 }} dataSource={data.edges.filter(e => flow.steps.includes(e.from) || flow.steps.includes(e.to))} columns={[
      { title: '实际登记关系', render: (_, e) => <>{data.nodes.find(n => n.id === e.from)?.label} → {data.nodes.find(n => n.id === e.to)?.label}<div><Tag>{flow.steps.includes(e.from) && flow.steps.includes(e.to) ? '流程内关系' : '跨流程影响'}</Tag></div></> },
      { title: '触发条件', dataIndex: 'condition' }, { title: '核查与禁止事项', dataIndex: 'check' },
      { title: '证据', render: (_, e) => <><Tag>{evidenceLabel[e.evidence]}</Tag><Sources items={e.sources} /></> },
    ]} /></Card>}
    <Drawer title={detail?.label} open={!!detail} onClose={() => setDetail(null)} width={560}>
      {detail && <><Typography.Paragraph><code>{detail.field}</code></Typography.Paragraph><Typography.Paragraph>{detail.note || '结构关系，请结合下方条件逐项核对。'}</Typography.Paragraph><Button onClick={() => { focus(detail); setDetail(null); }}>以此字段查看影响</Button><Typography.Title level={5}>来源与程序位置</Typography.Title><Sources items={detail.sources} /><Typography.Title level={5}>直接关联及验证要求</Typography.Title>{data.edges.filter(e => e.from === detail.id || e.to === detail.id).map(e => <Card size="small" key={e.id} style={{ marginBottom: 10 }}><strong>{data.nodes.find(n => n.id === e.from)?.label} → {data.nodes.find(n => n.id === e.to)?.label}</strong><p>{e.action} · {e.condition}</p><p>检查：{e.check}</p><Tag>{kindLabel[e.kind]}</Tag><Tag>{evidenceLabel[e.evidence]}</Tag><Sources items={e.sources} /></Card>)}</>}
    </Drawer>
  </Space>;
}
