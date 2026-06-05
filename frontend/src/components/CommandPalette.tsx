/**
 * 全局命令面板 ⌘K (Phase 10 Tier 3, 借鉴 Linear / Stripe).
 *
 * 按 Ctrl+K / ⌘+K 弹出, 输入关键字:
 *   - 订单号 / 客户 / 物料 / 产品 → 跳详情
 *   - 任何字 (200ms debounce) → 后端 /api/search 跨模型
 *
 * 同时支持 "动作快捷键": "新建退货" / "本周销售" 等高频动作直接执行.
 */
import { useEffect, useMemo, useState } from 'react';
import { AutoComplete, Empty, Input, Modal, Space, Tag, Typography } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { globalSearch } from '../api/client';

const QUICK_ACTIONS = [
  { label: '本周销售', url: '/reports', kw: '本周 销售 报表 sales' },
  { label: '资产 / 饼图', url: '/assets', kw: '资产 总额 饼图' },
  { label: '销售预测', url: '/forecast', kw: '销售 预测 forecast' },
  { label: '截图录单', url: '/screenshots', kw: '截图 录单 千牛' },
  { label: '退货 / 售后', url: '/aftersales', kw: '退货 售后' },
  { label: '客户列表', url: '/customers', kw: '客户 customer' },
  { label: '管理 / 系统监控', url: '/admin', kw: '管理 监控 admin' },
  { label: '订单看板', url: '/orders/kanban', kw: '订单 看板 kanban' },
  { label: '会计期间', url: '/admin?tab=accounting', kw: '会计 关账' },
  { label: '供应商评分', url: '/suppliers?tab=score', kw: '供应商 评分' },
];

const KIND_LABEL: Record<string, string> = {
  order: '订单', customer: '客户', material: '物料',
  product: '产品', supplier: '供应商', aftersales: '售后', alert: '告警',
};

const KIND_COLOR: Record<string, string> = {
  order: 'blue', customer: 'green', material: 'cyan',
  product: 'gold', supplier: 'magenta', aftersales: 'orange', alert: 'red',
};

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const navigate = useNavigate();

  // ⌘K / Ctrl+K 触发
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setOpen(true);
      }
      if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    const openHandler = () => setOpen(true);   // 顶栏可见搜索框点击触发
    window.addEventListener('keydown', handler);
    window.addEventListener('panse:open-search', openHandler);
    return () => {
      window.removeEventListener('keydown', handler);
      window.removeEventListener('panse:open-search', openHandler);
    };
  }, [open]);

  // 远端搜索 (debounce 200ms by useQuery enabled)
  const trimmed = q.trim();
  const { data: hits = [] } = useQuery({
    queryKey: ['command-search', trimmed],
    queryFn: () => globalSearch(trimmed, 30),
    enabled: trimmed.length >= 1 && open,
    staleTime: 30000,
  });

  const actionMatches = useMemo(() => {
    if (!trimmed) return QUICK_ACTIONS;
    const lower = trimmed.toLowerCase();
    return QUICK_ACTIONS.filter(
      (a) => a.label.toLowerCase().includes(lower) || a.kw.toLowerCase().includes(lower),
    );
  }, [trimmed]);

  const options = useMemo(() => {
    const out: any[] = [];
    if (actionMatches.length > 0) {
      out.push({
        label: <strong style={{ color: '#999' }}>动作</strong>,
        options: actionMatches.map((a) => ({
          value: `action:${a.url}`,
          label: (
            <Space>
              <Tag color="purple">动作</Tag>
              <span>{a.label}</span>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                → {a.url}
              </Typography.Text>
            </Space>
          ),
        })),
      });
    }
    if (hits.length > 0) {
      out.push({
        label: <strong style={{ color: '#999' }}>结果</strong>,
        options: hits.map((h) => ({
          value: `hit:${h.kind}:${h.id}:${h.url}`,
          label: (
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Space>
                <Tag color={KIND_COLOR[h.kind] ?? 'default'}>
                  {KIND_LABEL[h.kind] ?? h.kind}
                </Tag>
                <span>{h.title}</span>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                {h.subtitle}
              </Typography.Text>
            </Space>
          ),
        })),
      });
    }
    return out;
  }, [actionMatches, hits]);

  const onSelect = (value: string) => {
    const url = value.startsWith('action:')
      ? value.slice('action:'.length)
      : value.split(':').slice(3).join(':');
    setOpen(false);
    setQ('');
    if (url) navigate(url);
  };

  return (
    <Modal
      open={open}
      onCancel={() => setOpen(false)}
      footer={null}
      closable={false}
      width={680}
      style={{ top: 80 }}
      destroyOnClose
    >
      <AutoComplete
        autoFocus
        value={q}
        onChange={setQ}
        onSelect={onSelect}
        options={options}
        style={{ width: '100%' }}
        popupClassName="cmd-palette-dropdown"
      >
        <Input.Search
          size="large"
          placeholder="搜订单 / 客户 / 物料 / 产品 — 或输入动作名 (按 ESC 关闭)"
          enterButton="跳转"
          allowClear
        />
      </AutoComplete>
      {!q && (
        <Typography.Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
          快捷键: ⌘+K / Ctrl+K · 输入订单号 / 电话 / 关键字
        </Typography.Text>
      )}
      {q && options.length === 0 && (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
               description={`没找到 "${q}" 相关的`} />
      )}
    </Modal>
  );
}
