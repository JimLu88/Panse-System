/**
 * 顶部「异常」入口 + 紧急告警强弹 modal (原通知铃铛已于 2026-06-23 移除)。
 *
 * 用户拍板 2026-06-23: 顶部铃铛(通知中心)与右上角「异常」计数作用重复 → 去掉铃铛, 只留「异常」入口。
 * 文件名/导出名沿用 NotificationBell 以免改 App.tsx 引用; 现在只负责:
 *  - ① 右上角「异常 N」计数标签 (每 60s 轮询未处理异常数; 点击进 /exceptions)
 *  - ② critical / 退款待处理 告警的全局强弹 modal (安全网; 5 分钟冷却, 刷新不重弹)
 */
import React, { useEffect, useState } from 'react';
import { Badge, Button, List, Modal, Space, Tag, Typography } from 'antd';
import {
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
const MODAL_SHOWN_KEY = 'panse_alert_modal_shown_ids';

// C4: 已弹过的告警 id 持久化到 sessionStorage — 刷新页面不重弹
function loadShownIds(): Set<number> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(MODAL_SHOWN_KEY) || '[]'));
  } catch {
    return new Set();
  }
}

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

  // Phase 12: 订阅 SSE, 收到 alert 事件就刷新缓存 (供紧急强弹 modal 用)
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

  const [modalShownIds, setModalShownIds] = useState<Set<number>>(loadShownIds);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalAlerts, setModalAlerts] = useState<AlertItem[]>([]);

  // critical / 退款待处理 出现 → 强弹 modal (cooldown 5 分钟; F3 扩 refund_pending)
  useEffect(() => {
    const crits = alerts.filter((a) => a.severity === 'critical' || a.kind === 'refund_pending');
    if (crits.length === 0 || isInCooldown()) return;
    const unseen = crits.filter((a) => !modalShownIds.has(a.id));
    if (unseen.length > 0) {
      setModalAlerts(crits);
      setModalOpen(true);
      const next = new Set([...modalShownIds, ...crits.map((c) => c.id)]);
      setModalShownIds(next);
      try { sessionStorage.setItem(MODAL_SHOWN_KEY, JSON.stringify([...next])); } catch { /* 忽略 */ }
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

  const { data: excData } = useQuery({
    queryKey: ['open-exception-count'],
    queryFn: getOpenExceptionCount,
    refetchInterval: 60_000,
  });
  const openExceptions = excData?.count ?? 0;
  const excColor = openExceptions > 10 ? '#cf1322' : openExceptions > 3 ? '#fa8c16' : '#52c41a';

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
          (5 分钟内不再弹出; 可在右上角「异常」入口或对应页面继续处理)
        </Typography.Text>
      </Modal>
    </>
  );
}
