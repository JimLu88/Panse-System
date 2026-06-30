import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card, Tag, Progress, Timeline, Checkbox, Typography, Space, Spin, Button,
  message, Empty, Descriptions, InputNumber, Segmented,
} from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import {
  getNpdProjectDetail, toggleNpdTask, saveNpdInspection,
  type NpdProjectDetail, type NpdTimelineItem, type NpdTask, type NpdInspection,
} from '../api/client';
import NpdCostSupplierPanel from './NpdCostSupplierPanel';

function resultTag(r: string) {
  return r === 'pass' ? <Tag color="green">通过</Tag>
    : r === 'fail' ? <Tag color="red">不通过</Tag> : <Tag>待检</Tag>;
}

function InspectionRow({ item, onSaved }: { item: NpdInspection; onSaved: (it: NpdInspection) => void }) {
  const [reading, setReading] = useState<string>(item.reading ?? '');
  const [lo, setLo] = useState<string>(item.min_val ?? '');
  const [hi, setHi] = useState<string>(item.max_val ?? '');
  const [saving, setSaving] = useState(false);
  const isNum = item.check_type === 'numeric';

  const setResult = async (result: string) => {
    try {
      onSaved(await saveNpdInspection(item.id, { result }));
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    }
  };
  const saveNum = async () => {
    setSaving(true);
    try {
      onSaved(await saveNpdInspection(item.id, {
        reading: reading === '' ? null : String(reading),
        min_val: lo === '' ? null : lo,
        max_val: hi === '' ? null : hi,
      }));
      message.success('已保存');
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8, padding: '2px 0' }}>
      <span style={{ minWidth: 180 }}>
        {item.is_required && <Typography.Text type="danger">★ </Typography.Text>}{item.item_name}
      </span>
      {isNum ? (
        <Space size={4} wrap>
          <InputNumber
            size="small" placeholder="实测" style={{ width: 130 }}
            value={reading === '' ? null : Number(reading)}
            onChange={(v) => setReading(v == null ? '' : String(v))}
            addonAfter={item.unit || undefined}
          />
          <InputNumber size="small" placeholder="下限" style={{ width: 86 }}
            value={lo === '' ? null : Number(lo)} onChange={(v) => setLo(v == null ? '' : String(v))} />
          <InputNumber size="small" placeholder="上限" style={{ width: 86 }}
            value={hi === '' ? null : Number(hi)} onChange={(v) => setHi(v == null ? '' : String(v))} />
          <Button size="small" loading={saving} onClick={saveNum}>保存</Button>
        </Space>
      ) : (
        <Segmented
          size="small"
          value={item.result === 'pass' ? 'pass' : item.result === 'fail' ? 'fail' : ''}
          onChange={(v) => setResult(String(v))}
          options={[{ label: '通过', value: 'pass' }, { label: '不通过', value: 'fail' }]}
        />
      )}
      {resultTag(item.result)}
    </div>
  );
}

const GROUP_LABEL: Record<string, string> = {
  plan: '立项', design: '设计', sourcing: '寻源', prototype: '打样',
  production: '量产', launch: '上架', review: '复盘',
};
function priorityLabel(p: string) { return p === 'high' ? '高' : p === 'low' ? '低' : '中'; }

export default function NpdDetailPage() {
  const { id } = useParams<{ id: string }>();
  const nav = useNavigate();
  const [data, setData] = useState<NpdProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    if (!id) return;
    setLoading(true);
    try {
      setData(await getNpdProjectDetail(Number(id)));
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '加载失败');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  const onToggle = async (t: NpdTask, checked: boolean) => {
    try {
      await toggleNpdTask(t.id, checked);
      setData((prev) => prev && {
        ...prev,
        timeline: prev.timeline.map((row) => ({
          ...row,
          tasks: row.tasks.map((x) => (x.id === t.id
            ? { ...x, status: checked ? 'done' : 'open' } : x)),
        })),
      });
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '更新失败');
    }
  };

  const onInspSaved = (it: NpdInspection) => {
    setData((prev) => prev && {
      ...prev,
      timeline: prev.timeline.map((row) => ({
        ...row,
        inspections: row.inspections.map((x) => (x.id === it.id ? it : x)),
      })),
    });
  };

  if (loading) return <div style={{ padding: 24 }}><Spin /></div>;
  if (!data) return <div style={{ padding: 24 }}><Empty description="未找到项目" /></div>;

  const p = data.project;

  const taskList = (row: NpdTimelineItem) => {
    if (!row.tasks.length) return null;
    const undoneReq = row.tasks.filter((t) => t.is_required && t.status !== 'done').length;
    return (
      <div style={{ marginTop: 6 }}>
        {row.is_current && undoneReq > 0 && (
          <Typography.Text type="warning" style={{ fontSize: 12 }}>
            还有 {undoneReq} 个必做项未完成,完成后才能进入下一步
          </Typography.Text>
        )}
        <Space direction="vertical" size={2} style={{ width: '100%', marginTop: 4 }}>
          {row.tasks.map((t) => (
            <Checkbox
              key={t.id}
              checked={t.status === 'done'}
              onChange={(e) => onToggle(t, e.target.checked)}
            >
              {t.is_required && <Typography.Text type="danger">★ </Typography.Text>}
              {t.title}
              <Tag style={{ marginLeft: 6 }}>{t.category}</Tag>
            </Checkbox>
          ))}
        </Space>
      </div>
    );
  };

  const inspectionList = (row: NpdTimelineItem) => {
    if (!row.inspections.length) return null;
    const undone = row.inspections.filter((i) => i.is_required && i.result !== 'pass').length;
    return (
      <div style={{ marginTop: 8 }}>
        <Typography.Text strong style={{ fontSize: 12 }}>
          验收清单
          {row.is_current && undone > 0 && (
            <Typography.Text type="warning"> (还有 {undone} 项必检未通过)</Typography.Text>
          )}
        </Typography.Text>
        <div style={{ marginTop: 4 }}>
          {row.inspections.map((it) => (
            <InspectionRow key={it.id} item={it} onSaved={onInspSaved} />
          ))}
        </div>
      </div>
    );
  };

  const items = data.timeline.map((row) => {
    const done = row.instance_status === 'done';
    const color = row.is_current ? 'blue' : done ? 'green' : 'gray';
    return {
      color,
      children: (
        <div>
          <Space wrap size={6}>
            <Typography.Text strong={row.is_current}>
              {row.code} {row.name}
            </Typography.Text>
            <Tag>{GROUP_LABEL[row.group] || row.group}</Tag>
            {row.is_gate && <Tag color="gold">门</Tag>}
            {row.is_current && <Tag color="blue">当前</Tag>}
            {done && <Tag color="green">已完成</Tag>}
            {row.deadline && !done && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                截止 {dayjs(row.deadline).format('MM-DD')}
              </Typography.Text>
            )}
          </Space>
          {taskList(row)}
          {inspectionList(row)}
        </div>
      ),
    };
  });

  return (
    <div style={{ padding: 16, maxWidth: 920 }}>
      <Space style={{ marginBottom: 12 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => nav('/npd')}>返回</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>{p.name}</Typography.Title>
        <Tag>{p.code}</Tag>
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
          <Descriptions.Item label="当前阶段">
            {p.current_stage_name ? <Tag color="blue">{p.current_stage_name}</Tag> : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="进度">
            <Progress percent={p.percent_done} size="small" style={{ width: 140 }} />
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            {p.state === 'done' ? <Tag color="green">完成</Tag>
              : p.state === 'rework' ? <Tag color="orange">返工</Tag> : <Tag>进行</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="品类">{p.category || '-'}</Descriptions.Item>
          <Descriptions.Item label="负责人">{p.owner || '-'}</Descriptions.Item>
          <Descriptions.Item label="优先级">{priorityLabel(p.priority)}</Descriptions.Item>
          <Descriptions.Item label="目标售价">{p.target_price ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="目标毛利率">
            {p.target_margin_rate != null ? `${(Number(p.target_margin_rate) * 100).toFixed(0)}%` : '-'}
          </Descriptions.Item>
          <Descriptions.Item label="目标上市日">{p.target_launch_date || '-'}</Descriptions.Item>
        </Descriptions>
        {p.remark && <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>{p.remark}</Typography.Paragraph>}
      </Card>

      <Card size="small" title="阶段进度 / 待办">
        <Timeline items={items} />
      </Card>

      <NpdCostSupplierPanel detail={data} projectId={p.id} onChange={load} />
    </div>
  );
}
