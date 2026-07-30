import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  CloudSyncOutlined,
  DownloadOutlined,
  ExportOutlined,
  LockOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  downloadFactoryDispatchWorkbook,
  getFactoryDispatchSettings,
  getFactoryDispatchSummary,
  syncFactoryDispatch,
  updateFactoryDispatchSettings,
} from '../api/orders';
import { triggerBlobDownload } from '../utils/download';

const URGENCY_COLORS: Record<string, string> = {
  已超期: 'red',
  非常紧急: 'volcano',
  紧急: 'orange',
  正常安排: 'green',
  远期单: 'purple',
  '已完成/已作废': 'default',
};

export default function FactoryDispatchSyncTab() {
  const qc = useQueryClient();
  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ['factory-dispatch-settings'],
    queryFn: getFactoryDispatchSettings,
  });
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['factory-dispatch-summary'],
    queryFn: getFactoryDispatchSummary,
  });

  const updateMut = useMutation({
    mutationFn: updateFactoryDispatchSettings,
    onSuccess: () => {
      message.success('同步设置已保存');
      qc.invalidateQueries({ queryKey: ['factory-dispatch-settings'] });
      qc.invalidateQueries({ queryKey: ['factory-dispatch-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '保存失败'),
  });

  const syncMut = useMutation({
    mutationFn: syncFactoryDispatch,
    onSuccess: (result) => {
      if (result.ok) {
        message.success(
          `同步完成：共 ${result.rows} 单，新增 ${result.created}，更新 ${result.updated}`,
        );
      } else {
        message.error(`同步失败：${(result.errors || []).join('；') || '未知原因'}`);
      }
      qc.invalidateQueries({ queryKey: ['factory-dispatch-summary'] });
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '同步失败'),
  });

  const exportMut = useMutation({
    mutationFn: () => downloadFactoryDispatchWorkbook(true),
    onSuccess: (blob) => {
      const today = new Date().toISOString().slice(0, 10);
      triggerBlobDownload(blob, `工厂系统下单表_${today}.xlsx`);
      message.success('表格已导出（含产品图）');
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '导出失败'),
  });

  const feishuUrl = settings
    ? `https://my.feishu.cn/base/${settings.app_token}?table=${settings.table_id}`
    : '';

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        icon={<LockOutlined />}
        message="固定为单向同步：ERP → 飞书"
        description="飞书表用于工厂和运营查看。飞书中的编辑不会回写 ERP；通用双向同步和飞书事件回调也被后端拦截。"
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="同步设置" loading={settingsLoading}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="自动同步">
                <Space>
                  <Switch
                    checked={settings?.auto_enabled ?? true}
                    loading={updateMut.isPending}
                    onChange={(checked) => updateMut.mutate({ auto_enabled: checked })}
                  />
                  <Typography.Text type="secondary">
                    订单更新和下单图处理完成后自动同步，另有每 6 小时兜底
                  </Typography.Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="同步产品图">
                <Space>
                  <Switch
                    checked={settings?.include_images ?? true}
                    loading={updateMut.isPending}
                    onChange={(checked) => updateMut.mutate({ include_images: checked })}
                  />
                  <Typography.Text type="secondary">
                    关闭后只同步文字和状态，速度更快
                  </Typography.Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="同步方向">
                <Tag color="blue" icon={<LockOutlined />}>仅 ERP → 飞书</Tag>
              </Descriptions.Item>
            </Descriptions>
            <Space wrap style={{ marginTop: 16 }}>
              <Button
                type="primary"
                icon={<CloudSyncOutlined />}
                loading={syncMut.isPending}
                onClick={() => syncMut.mutate()}
              >
                立即同步
              </Button>
              <Button
                icon={<DownloadOutlined />}
                loading={exportMut.isPending}
                onClick={() => exportMut.mutate()}
              >
                导出系统下单表
              </Button>
              <Button
                icon={<ExportOutlined />}
                disabled={!feishuUrl}
                onClick={() => window.open(feishuUrl, '_blank', 'noopener,noreferrer')}
              >
                打开飞书表
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="当前同步范围" loading={summaryLoading}>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="订单">{summary?.rows ?? 0} 单</Descriptions.Item>
              <Descriptions.Item label="定制单">{summary?.custom_count ?? 0} 单</Descriptions.Item>
              <Descriptions.Item label="需拍照通知爱群">
                {summary?.photo_notice_count ?? 0} 单
              </Descriptions.Item>
              <Descriptions.Item label="分组">
                {Object.entries(summary?.group_counts ?? {}).map(([name, count]) => (
                  <Tag key={name}>{name} {count}</Tag>
                ))}
              </Descriptions.Item>
            </Descriptions>
            <div style={{ marginTop: 12 }}>
              <Typography.Text type="secondary">交期紧急度：</Typography.Text>
              <Space wrap size={[4, 8]} style={{ marginLeft: 8 }}>
                {Object.entries(summary?.urgency_counts ?? {}).map(([name, count]) => (
                  <Tag color={URGENCY_COLORS[name]} key={name}>{name} {count}</Tag>
                ))}
              </Space>
            </div>
          </Card>
        </Col>
      </Row>

      <Alert
        type="success"
        showIcon
        message="发货安排已联动拍照需求"
        description="订单备注命中拍照要求时，飞书“发货安排”会显示“需拍照后通知爱群”，不会再显示“做好直接发货”。"
      />
    </Space>
  );
}
