/**
 * 顶部通知铃铛 (Phase 1B, 业务需求 4/5/6/8/9/11).
 *
 * - 每 30s 轮询 /api/alerts/active 拉未解决告警
 * - 角标显示总数; critical 数显示在红色徽标里
 * - 点击展开下拉; 高优 critical 不可 dismiss (sticky)
 * - 全局 modal: critical 第一次出现强弹一次, 用户点 "知道了" 后 5 分钟内不再弹
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Dropdown,
  Empty,
  List,
  Modal,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  BellOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertItem, dismissAlert, fetchActiveAlerts, getOpenExceptionCount } from '../api/client';
import { Link } from 'react-router-dom';

const SEVERITY_COLOR: Record<string, string> = {
  info: 'blue', warn: 'orange', critical: 'red',
};
const SEVERITY_ICON: Record<string, React.ReactNode> = {
  info: <CheckCircleOutlined style={{ color: '#1677ff' }} />,
  warn: <WarningOutlined style={{ color: '#fa8c16' }} />,
  critical: <CloseCircleOutlined style={{ color: '#cf1322' }} />,
};

const MODAL_COOLDOWN_KEY = 'panse_alert_modal_seen';

function isInCooldown(): boolean {
  const raw = localStorage.getItem(MODAL_COOLDOWN_KEY);
  if (!raw) return false;
  try {
    const seen = JSON.parse(raw) as { ts: number; ids: number[] };
    return Date.now() - seen.ts < 5 * 60 * 1000;
  } catch {
    return false;
  }
}

function markCooldown(ids: number[]): void {
  localStorage.setItem(MODAL_COOLDOWN_KEY, JSON.stringify({ ts: Date.now(), ids }));
}

export default function NotificationBell() {
  const qc = useQueryClient();
  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts-active'],
    queryFn: () => fetchActiveAlerts({ limit: 100 }),
    // Phase 12: SSE 实时推送替代 30s 轮询. 5 分钟兜底刷一次防万一断开
    refetchInterval: 5 * 60 * 1000,
  });

  // Phase 12: 订阅 SSE, 收到 alert 事件就刷新缓存
  useEffect(() => {
    const token = localStorage.getItem('panse_token');
    if (!token) return;
    // 浏览器 EventSource 不支持自定义 header, token 作 query 参数传给 nginx 透传后端
    const es = new EventSource(`/api/alerts/stream`);
    const refresh = () => qc.invalidateQueries({ queryKey: ['alerts-active'] });
    es.addEventListener('alert.upserted', refresh);
    es.addEventListener('alert.resolved', refresh);
    es.onerror = () => {
      // 断线时 EventSource 会自动重连, 不需手动处理
    };
    return () => es.close();
  }, [qc]);

  const counts = useMemo(() => {
    const out = { info: 0, warn: 0, critical: 0 };
    alerts.forEach((a) => {
      out[a.severity] = (out[a.severity] ?? 0) + 1;
    });
    return out;
  }, [alerts]);

  const [modalShownIds, setModalShownIds] = useState<Set<number>>(new Set());
  const [modalOpen, setModalOpen] = useState(false);
  const [modalAlerts, setModalAlerts] = useState<AlertItem[]>([]);

  // critical 出现 → 强弹 modal (cooldown 5 分钟)
  useEffect(() => {
    const crits = alerts.filter((a) => a.severity === 'critical');
    if (crits.length === 0 || isInCooldown()) return;
    const unseen = crits.filter((a) => !modalShownIds.has(a.id));
    if (unseen.length > 0) {
      setModalAlerts(crits);
      setModalOpen(true);
      setModalShownIds(new Set([...modalShownIds, ...crits.map((c) => c.id)]));
      markCooldown(crits.map((c) => c.id));
    }
  }, [alerts, modalShownIds]);

  const dismissMut = useMutation({
    mutationFn: (id: number) => dismissAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts-active'] }),
  });

  const itemFor = (a: AlertItem) => (
    <List.Item
      key={a.id}
      actions={
        a.sticky
          ? [<Tag color="purple" key="sticky">需解决根因</Tag>]
          : [
              <Button
                size="small"
                type="link"
                key="dismiss"
                onClick={() => dismissMut.mutate(a.id)}
              >
                已知晓
              </Button>,
            ]
      }
    >
      <List.Item.Meta
        avatar={SEVERITY_ICON[a.severity]}
        title={
          <Space>
            <Tag color={SEVERITY_COLOR[a.severity]}>{a.severity.toUpperCase()}</Tag>
            <span>{a.title}</span>
          </Space>
        }
        description={
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            {a.body && <Typography.Text style={{ fontSize: 12 }}>{a.body}</Typography.Text>}
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {new Date(a.created_at).toLocaleString('zh-CN')}
              {a.related_url && (
                <>
                  {' · '}
                  <a href={`#${a.related_url}`} onClick={(e) => {
                    e.preventDefault();
                    window.location.hash = a.related_url || '';
                  }}>
                    去处理
                  </a>
                </>
              )}
            </Typography.Text>
          </Space>
        }
      />
    </List.Item>
  );

  const dropdown = (
    <div style={{ width: 420, maxHeight: 540, overflow: 'auto',
                  background: '#fff', boxShadow: '0 6px 16px -8px rgba(0,0,0,.2)',
                  borderRadius: 8 }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0',
                    fontWeight: 600 }}>
        通知中心
        <span style={{ float: 'right', fontSize: 12, color: '#999', fontWeight: 'normal' }}>
          critical {counts.critical} · warn {counts.warn} · info {counts.info}
        </span>
      </div>
      {alerts.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目前没有未处理告警" />
      ) : (
        <List dataSource={alerts} renderItem={itemFor} />
      )}
    </div>
  );

  const { data: excData } = useQuery({
    queryKey: ['open-exception-count'],
    queryFn: getOpenExceptionCount,
    refetchInterval: 60_000,
  });
  const openExceptions = excData?.count ?? 0;
  const excColor = openExceptions > 10 ? '#cf1322' : openExceptions > 3 ? '#fa8c16' : '#52c41a';

  const total = alerts.length;
  const colorByPriority = counts.critical > 0 ? '#cf1322' : counts.warn > 0 ? '#fa8c16' : '#1677ff';

  return (
    <>
      {openExceptions > 0 && (
        <Link to="/exceptions" style={{ marginRight: 8 }}>
          <Badge count={openExceptions} size="small" style={{ backgroundColor: excColor }} title={`${openExceptions} 条未处理异常`}>
            <Tag color={openExceptions > 10 ? 'red' : openExceptions > 3 ? 'orange' : 'green'}
                 style={{ cursor: 'pointer', margin: 0 }}>
              异常 {openExceptions}
            </Tag>
          </Badge>
        </Link>
      )}
      <Dropdown popupRender={() => dropdown} trigger={['click']} placement="bottomRight">
        <Button
          type="text"
          icon={
            <Badge count={total} size="small" style={{ backgroundColor: colorByPriority }}>
              <BellOutlined style={{ color: 'white', fontSize: 18 }} />
            </Badge>
          }
          style={{ marginRight: 16 }}
        />
      </Dropdown>
      <Modal
        title={<Space><CloseCircleOutlined style={{ color: '#cf1322' }} />紧急告警</Space>}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => setModalOpen(false)}
        okText="知道了"
        cancelButtonProps={{ style: { display: 'none' } }}
        width={600}
      >
        <List dataSource={modalAlerts} renderItem={itemFor} />
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          (5 分钟内不再弹出, 在右上角铃铛可继续查看)
        </Typography.Text>
      </Modal>
    </>
  );
}
