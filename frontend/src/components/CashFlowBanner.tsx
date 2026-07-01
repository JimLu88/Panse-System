/**
 * 剩余流水 / 可用资金 卡片 — 原在运营大盘, 2026-07-01 移到「报表」页顶部。
 * 复用 /api/finance/cash-flow, 点击进 /cash-flow 完整页; 含数据新鲜度红绿灯。
 */
import { Card, Col, Grid, Row, Spin, Tag, Tooltip } from 'antd';
import { DollarOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getCashFlow, type CashFlowSummary, type CashFlowFreshness } from '../api/finance';

const M = {
  violet: '#8b5cf6', indigo: '#6366f1', emerald: '#10b981', rose: '#f43f5e',
  ink: '#1e293b', sub: '#94a3b8',
};
const cardStyle = { borderRadius: 16, border: '1px solid #eef0f4', boxShadow: '0 1px 2px rgba(15,23,42,.04)' };
function MCard({ children, style, ...rest }: any) {
  return <Card size="small" bordered={false} style={{ ...cardStyle, ...style }} {...rest}>{children}</Card>;
}
function money(v: number) {
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`;
}
const FRESH_DOT: Record<string, { dot: string; color: string }> = {
  fresh: { dot: '🟢', color: 'success' }, aging: { dot: '🟡', color: 'warning' },
  stale: { dot: '🔴', color: 'error' }, unknown: { dot: '⚪', color: 'default' },
};
function freshAgo(f: CashFlowFreshness) {
  if (f.days_ago == null) return '无记录';
  if (f.days_ago === 0) return '今天';
  return `${f.days_ago} 天前`;
}

export default function CashFlowBanner() {
  const nav = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;
  const { data, isLoading } = useQuery<CashFlowSummary>({
    queryKey: ['cash-flow'], queryFn: getCashFlow, refetchInterval: 60_000,
  });
  if (isLoading || !data) {
    return (
      <MCard style={{ marginBottom: 16 }}>
        <div style={{ height: 96, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Spin /></div>
      </MCard>
    );
  }
  const totalNum = Number(data.total);
  const hasStale = data.freshness.some((f) => f.status === 'stale');
  const invest = data.subtractions.find((s) => s.key === 'total_investment');
  return (
    <MCard
      hoverable={!isMobile}
      onClick={isMobile ? undefined : () => nav('/cash-flow')}
      style={{
        marginBottom: 16, cursor: isMobile ? 'default' : 'pointer',
        background: 'linear-gradient(135deg,#ffffff 0%,#f5f3ff 100%)',
        borderColor: hasStale ? '#fecaca' : '#e9d5ff',
      }}
    >
      <Row align="middle" gutter={[16, 12]}>
        <Col xs={24} md={10}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <DollarOutlined style={{ color: M.violet }} />
            <span style={{ color: M.sub, fontSize: 13, fontWeight: 600 }}>剩余流水 · 可用资金（实时）</span>
            {hasStale && <Tag color="error" style={{ borderRadius: 8 }}>数据偏旧</Tag>}
          </div>
          <div style={{ color: totalNum >= 0 ? M.emerald : M.rose, fontWeight: 800, fontSize: 30, letterSpacing: '-0.01em', marginTop: 2 }}>
            {money(totalNum)}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: M.sub }}>
            <span style={{ color: M.emerald }}>↑ 加项 {money(Number(data.total_additions))}</span>
            <span style={{ margin: '0 8px' }}>·</span>
            <span style={{ color: M.rose }}>↓ 减项 {money(Number(data.total_subtractions))}</span>
            {invest && (
              <>
                <span style={{ margin: '0 8px' }}>·</span>
                <span>总投资费用 {money(Number(invest.amount))}</span>
              </>
            )}
          </div>
        </Col>
        <Col xs={24} md={14}>
          {isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {data.freshness.map((f) =>
                /投资|保证金/.test(f.source) ? (
                  <div key={f.source}
                    onClick={(e) => { e.stopPropagation(); nav('/cash-flow'); }}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '9px 12px', borderRadius: 10, background: '#eef4ff',
                      color: M.indigo, fontSize: 13, fontWeight: 600,
                    }}>
                    <span>{f.source}</span><span>更新 →</span>
                  </div>
                ) : (
                  <div key={f.source}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '9px 12px', borderRadius: 10,
                      background: '#f8fafc', border: '1px solid #eef0f4', fontSize: 13,
                    }}>
                    <span style={{ color: M.ink, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {FRESH_DOT[f.status]?.dot} {f.source}
                    </span>
                    <span style={{ color: M.sub, flexShrink: 0, marginLeft: 8 }}>{freshAgo(f)}</span>
                  </div>
                ),
              )}
              <div onClick={() => nav('/cash-flow')}
                style={{ textAlign: 'center', marginTop: 4, padding: '8px', fontSize: 13, color: M.indigo, fontWeight: 600, cursor: 'pointer' }}>
                点击查看完整明细 →
              </div>
            </div>
          ) : (
            <>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'flex-end' }}>
                {data.freshness.map((f) =>
                  /投资|保证金/.test(f.source) ? (
                    <Tag key={f.source} color="blue" style={{ borderRadius: 8, marginInlineEnd: 0, cursor: 'pointer' }}
                      onClick={(e) => { e.stopPropagation(); nav('/cash-flow'); }}>
                      {f.source} · 更新 →
                    </Tag>
                  ) : (
                    <Tooltip key={f.source} title={`数据截至 ${f.as_of ? new Date(f.as_of).toLocaleDateString('zh-CN') : '无记录'}`}>
                      <Tag color={FRESH_DOT[f.status]?.color || 'default'} style={{ borderRadius: 8, marginInlineEnd: 0 }}>
                        {FRESH_DOT[f.status]?.dot} {f.source} · {freshAgo(f)}
                      </Tag>
                    </Tooltip>
                  ),
                )}
              </div>
              <div style={{ textAlign: 'right', marginTop: 8, fontSize: 12, color: M.indigo }}>点击查看完整明细 →</div>
            </>
          )}
        </Col>
      </Row>
    </MCard>
  );
}
