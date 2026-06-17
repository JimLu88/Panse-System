/**
 * 财务系数设置 (管理) — 平台手续费/活动抽成/税率, 全系统会计成本口径用它。
 * 高危: 改动会影响所有利润数字 → 2 次严重警告 + 登录密码确认 (用户拍板 2026-06-17)。
 */
import { useEffect, useState } from 'react';
import { Alert, Button, Card, DatePicker, Input, InputNumber, Modal, Space, Typography, message } from 'antd';
import dayjs from 'dayjs';
import { useQuery } from '@tanstack/react-query';
import { getFinancialCoefficients, putFinancialCoefficients } from '../api/finance';

const { Text } = Typography;
const pct = (s: string | undefined) => (s == null ? 0 : Number(s) * 100);

export default function FinancialCoefficientsCard() {
  const { data, refetch } = useQuery({ queryKey: ['fin-coef'], queryFn: getFinancialCoefficients });
  const [handling, setHandling] = useState<number | null>(null);   // %
  const [activity, setActivity] = useState<number | null>(null);   // %
  const [tax, setTax] = useState<number | null>(null);             // %
  const [since, setSince] = useState<string>('2026-05-01');
  const [pwOpen, setPwOpen] = useState(false);
  const [pw, setPw] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (data) {
      setHandling(pct(data.fin_platform_handling_rate));
      setActivity(pct(data.fin_platform_activity_rate));
      setTax(pct(data.fin_tax_rate));
      setSince(data.fin_platform_activity_since || '2026-05-01');
    }
  }, [data]);

  // 2 次严重警告 → 输密码
  const onSave = () => {
    Modal.confirm({
      title: '⚠️ 严重警告 (1/2)', okText: '我知道, 继续', okButtonProps: { danger: true }, cancelText: '取消',
      content: '财务系数影响【全系统所有利润数字】(销售汇总/月度经营/数据大盘/对账…)。改错会让所有报表的成本与利润全部偏掉。',
      onOk: () => Modal.confirm({
        title: '⚠️ 再次确认 (2/2)', okText: '确认修改', okButtonProps: { danger: true }, cancelText: '取消',
        content: '你确定现在要修改财务系数吗? 修改后立即对全系统生效。',
        onOk: () => { setPw(''); setPwOpen(true); },
      }),
    });
  };

  const submit = async () => {
    if (!pw) { message.error('请输入登录密码'); return; }
    setSaving(true);
    try {
      await putFinancialCoefficients({
        fin_platform_handling_rate: String((handling ?? 0) / 100),
        fin_platform_activity_rate: String((activity ?? 0) / 100),
        fin_tax_rate: String((tax ?? 0) / 100),
        fin_platform_activity_since: since,
        password: pw,
      });
      message.success('财务系数已修改, 全系统已生效');
      setPwOpen(false); setPw(''); refetch();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '修改失败');
    } finally { setSaving(false); }
  };

  return (
    <Card size="small" title="💰 财务系数设置 (会计成本口径)" style={{ marginTop: 16 }}>
      <Alert type="warning" showIcon style={{ marginBottom: 12 }}
        message="这组系数决定全系统怎么算成本和利润"
        description="会计总成本 = 物理成本+物流+安装/上楼+售后 + 平台扣点(手续费+活动抽成, 或实付−店铺实收) + 税费。改动需 2 次警告 + 密码。" />
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap size="large">
          <span><Text>平台手续费率</Text>{' '}
            <InputNumber min={0} max={50} step={0.1} value={handling} onChange={setHandling} addonAfter="%" style={{ width: 120 }} /></span>
          <span><Text>平台活动抽成率</Text>{' '}
            <InputNumber min={0} max={50} step={0.1} value={activity} onChange={setActivity} addonAfter="%" style={{ width: 120 }} /></span>
          <span><Text>税率</Text>{' '}
            <InputNumber min={0} max={50} step={0.1} value={tax} onChange={setTax} addonAfter="%" style={{ width: 120 }} /></span>
          <span><Text>活动抽成生效起始日</Text>{' '}
            <DatePicker value={since ? dayjs(since) : null}
              onChange={(d) => setSince(d ? d.format('YYYY-MM-DD') : '2026-05-01')} /></span>
        </Space>
        <Button danger onClick={onSave}>保存财务系数（需 2 次警告 + 密码）</Button>
      </Space>

      <Modal title="输入登录密码确认" open={pwOpen} onCancel={() => setPwOpen(false)}
        onOk={submit} confirmLoading={saving} okText="确认修改" okButtonProps={{ danger: true }}>
        <Text type="secondary">最后一步: 输入你的登录密码以确认修改财务系数。</Text>
        <Input.Password style={{ marginTop: 8 }} placeholder="登录密码"
          value={pw} onChange={(e) => setPw(e.target.value)} onPressEnter={submit} />
      </Modal>
    </Card>
  );
}
