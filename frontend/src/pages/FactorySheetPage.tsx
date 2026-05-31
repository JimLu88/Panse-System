import { useEffect } from 'react';
import { Alert, Button, Space, Spin } from 'antd';
import { PrinterOutlined, WarningOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getFactorySheet } from '../api/client';
import FactorySheetCard from '../components/FactorySheetCard';

export default function FactorySheetPage() {
  const { orderId } = useParams<{ orderId: string }>();
  const nav = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ['factory-sheet', orderId],
    queryFn: () => getFactorySheet(Number(orderId)),
    enabled: !!orderId,
  });

  useEffect(() => {
    document.title = data ? `下单图 - ${data.sheet_title}` : '下单图';
  }, [data]);

  if (isLoading) return <Spin />;
  if (error || !data) return <Alert type="error" message="加载失败" />;

  const hasError = data.warnings.some((w) => w.severity === 'error');

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space style={{ justifyContent: 'space-between', width: '100%' }} className="no-print">
          <Button onClick={() => nav('/orders')}>← 返回订单</Button>
          <Button
            type="primary"
            icon={<PrinterOutlined />}
            disabled={hasError}
            onClick={() => window.print()}
          >
            打印下单图
          </Button>
        </Space>

        {hasError && (
          <Alert
            type="error"
            showIcon
            icon={<WarningOutlined />}
            message="存在严重问题, 请先解决再打印"
            description={
              <ul style={{ marginBottom: 0 }}>
                {data.warnings
                  .filter((w) => w.severity === 'error')
                  .map((w) => <li key={w.code}>{w.message}</li>)}
              </ul>
            }
          />
        )}
        {data.warnings
          .filter((w) => w.severity !== 'error')
          .map((w) => (
            <Alert key={w.code} type="warning" showIcon message={w.message} />
          ))}

        <FactorySheetCard data={data} />
      </Space>

      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { background: white; }
          .factory-sheet-card { border: none !important; padding: 0 !important; }
        }
      `}</style>
    </div>
  );
}
