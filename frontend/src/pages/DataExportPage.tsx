/**
 * Excel 导出 (工具) — 一键把系统所有类目按 Sheet 全量导出。
 *
 * - 每个数据表(类目)一个 Sheet, 全行导出; 末列「异常批注」写该行未处理异常 + 单元格批注。
 * - 导出后自动存进「资料存档库」(类型: 全量导出); 超过 30 份自动删最早的一份。
 */
import { useState } from 'react';
import { Alert, Button, Card, Space, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import { api } from '../api/client';

export default function DataExportPage() {
  const [loading, setLoading] = useState(false);

  const handleExport = async () => {
    setLoading(true);
    try {
      // 全量导出要建 28 个 Sheet + 公式 + 样式, 后端要几十秒; 默认 30s 会超时报"导出失败",
      // 但服务端其实已生成成功并入档 → 调到 5 分钟 (用户拍板 2026-06-17)
      const resp = await api.post('/api/exports/full', null, { responseType: 'blob', timeout: 300000 });
      const sheets = resp.headers['x-export-sheets'];
      const rotated = resp.headers['x-export-rotated'];
      const url = window.URL.createObjectURL(resp.data as Blob);
      const a = document.createElement('a');
      a.href = url;
      const today = new Date().toISOString().slice(0, 10);
      a.download = `全量导出_${today}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      message.success(
        `导出完成：${sheets ?? '?'} 个类目已分 Sheet 导出` +
        (rotated && Number(rotated) > 0 ? `；存档轮转删除最早 ${rotated} 份` : '；已存入资料存档库'),
      );
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '导出失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Typography.Title level={4} style={{ margin: 0 }}>Excel 导出</Typography.Title>
      <Alert
        type="info" showIcon
        message="把系统所有类目一次性导出为一个 Excel（每个类目一个 Sheet）。"
        description={
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            <li>按系统当前所有数据表分 Sheet 全量导出（产品 / 订单 / 物料 / BOM / 库存 / 支付宝流水 / 售后 …）。</li>
            <li>每个 Sheet 最后一列「异常批注」：该行若有未处理异常，写进去并加单元格批注。</li>
            <li>导出后自动存入「资料存档库」（类型：全量导出）；超过 30 份时自动删除日期最早的一份。</li>
          </ul>
        }
      />
      <Card>
        <Space direction="vertical" size="middle">
          <Button type="primary" size="large" icon={<DownloadOutlined />}
            loading={loading} onClick={handleExport}>
            导出全部类目 Excel
          </Button>
          <Typography.Text type="secondary">
            导出历史可在「工具 → 资料存档库 → 全量导出」查看与重新下载（自动留存最近 30 份）。
          </Typography.Text>
        </Space>
      </Card>
    </Space>
  );
}
