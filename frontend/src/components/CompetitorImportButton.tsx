import { Button, Upload, message } from 'antd';
import { UploadOutlined } from '@ant-design/icons';

interface CompetitorImportButtonProps {
  onDone?: () => void;
  label?: string;
}

/**
 * 复用的「导入竞品价 xlsx」按钮 (三个入口共用: 竞品价库 tab / 定制报价页 / 通用Excel导入页)。
 * 走 /api/customization/competitors/import (自动识别表头, 按 店铺+SKU 去重, 原文件存档)。
 */
export function CompetitorImportButton({ onDone, label = '导入竞品价 xlsx' }: CompetitorImportButtonProps) {
  return (
    <Upload
      accept=".xlsx"
      showUploadList={false}
      beforeUpload={async (file) => {
        const fd = new FormData();
        fd.append('file', file as File);
        try {
          const { api } = await import('../api/client');
          const r = await api.post('/api/customization/competitors/import', fd, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
          message.success(
            `竞品价库导入完成: 新增 ${r.data.inserted}, 更新 ${r.data.updated}, 跳过 ${r.data.skipped}; 原文件已存档`,
          );
          onDone?.();
        } catch (e: any) {
          message.error(e?.response?.data?.detail ?? '导入失败');
        }
        return false;
      }}
    >
      <Button icon={<UploadOutlined />}>{label}</Button>
    </Upload>
  );
}
