/**
 * Plan F4: 紧急缺料门 — 采购操作入口先把未处理的 critical 缺料亮出来。
 *
 * 顶部红色横幅常驻 + 每会话首次进入弹一次确认 (sessionStorage 防重弹)。
 * 挂在 PurchasesPage / AccessoryPurchasePage 顶部。
 */
import { useEffect, useRef } from 'react';
import { Alert, Modal } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { fetchActiveAlerts } from '../api/client';

const SEEN_KEY = 'panse_shortage_gate_seen';

export default function UrgentShortageGate() {
  const { data: alerts = [] } = useQuery({
    queryKey: ['alerts-active-shortage-gate'],
    queryFn: () => fetchActiveAlerts({ limit: 100 }),
  });
  const shortages = alerts.filter(
    (a) => a.severity === 'critical' && a.kind === 'low_stock_part',
  );
  const shownRef = useRef(false);

  useEffect(() => {
    if (!shortages.length || shownRef.current) return;
    if (sessionStorage.getItem(SEEN_KEY)) return;
    shownRef.current = true;
    Modal.warning({
      title: `有 ${shortages.length} 项配件缺货未处理`,
      width: 560,
      okText: '知道了，继续操作',
      onOk: () => { try { sessionStorage.setItem(SEEN_KEY, '1'); } catch { /* 忽略 */ } },
      content: (
        <ul style={{ paddingLeft: 18 }}>
          {shortages.slice(0, 8).map((a) => (
            <li key={a.id}>{a.title}</li>
          ))}
          {shortages.length > 8 && <li>… 等 {shortages.length} 项</li>}
        </ul>
      ),
    });
  }, [shortages.length]);

  if (!shortages.length) return null;
  return (
    <Alert
      type="error" showIcon style={{ marginBottom: 12 }}
      message={`紧急缺料 ${shortages.length} 项 — 采购/定制前请优先处理`}
      description={shortages.slice(0, 5).map((a) => a.title).join('；')
        + (shortages.length > 5 ? ` 等 ${shortages.length} 项` : '')}
    />
  );
}
