import { useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import { RobotOutlined, SendOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ScannerResult,
  aiChat,
  aiStatus,
  runAllScanners,
} from '../api/client';

interface Turn {
  role: 'user' | 'ai';
  text: string;
  tokens?: { in: number; out: number; cacheRead: number } | null;
  error?: string | null;
}

export default function AiAssistantPage() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);

  const { data: status } = useQuery({ queryKey: ['ai-status'], queryFn: aiStatus });

  const chatMut = useMutation({
    mutationFn: (msg: string) => aiChat(msg, 'web-chat'),
    onSuccess: (res, msg) => {
      setTurns((prev) => [
        ...prev,
        { role: 'user', text: msg },
        {
          role: 'ai',
          text: res.text ?? '',
          tokens: res.input_tokens != null
            ? { in: res.input_tokens, out: res.output_tokens ?? 0, cacheRead: res.cache_read_tokens ?? 0 }
            : null,
          error: res.error,
        },
      ]);
      setDraft('');
    },
    onError: (e: any) => message.error(e?.response?.data?.detail ?? '调用失败'),
  });

  const scanMut = useMutation({
    mutationFn: () => runAllScanners(false),
    onSuccess: (res) => {
      const total = Object.values(res).reduce((s, r) => s + r.written, 0);
      const skipped = Object.values(res).reduce((s, r) => s + r.skipped_duplicate, 0);
      message.success(`扫描完成：新增异常 ${total} 条，去重跳过 ${skipped} 条`);
      qc.invalidateQueries({ queryKey: ['exceptions'] });
    },
  });

  return (
    <Space direction="vertical" style={{ width: '100%', maxWidth: 900 }} size="middle">
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          <RobotOutlined /> AI 辅助助手
        </Typography.Title>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          onClick={() => scanMut.mutate()}
          loading={scanMut.isPending}
        >
          全量异常扫描
        </Button>
      </Space>

      {!status?.configured && (
        <Alert
          type="warning"
          showIcon
          message="AI 未配置"
          description={
            <span>
              当前没设 <code>ANTHROPIC_API_KEY</code>。后端会返回提示但不会崩。
              {' '}在 <code>backend/.env</code> 加 <code>ANTHROPIC_API_KEY=sk-ant-...</code> 后重启 API。
              当前模型: <Tag>{status?.model}</Tag>
            </span>
          }
        />
      )}

      <Card title="对话" size="small">
        <div style={{ maxHeight: 500, overflow: 'auto', marginBottom: 12 }}>
          {turns.length === 0 ? (
            <Empty
              description={
                <span style={{ color: '#999' }}>
                  问点什么？例如：<br />
                  · 「定制物料没价格的处理流程是什么？」<br />
                  · 「为什么会有 800 条 dangling_product_code 异常？」<br />
                  · 「±0.5% 内的对账差异要不要抹平？」
                </span>
              }
            />
          ) : (
            turns.map((t, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  justifyContent: t.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: 12,
                }}
              >
                <div
                  style={{
                    maxWidth: '80%',
                    background: t.role === 'user' ? '#1677ff' : '#f0f0f0',
                    color: t.role === 'user' ? 'white' : 'black',
                    padding: '8px 12px',
                    borderRadius: 8,
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {t.error ? (
                    <Alert type="error" message={t.error} style={{ margin: -4 }} />
                  ) : (
                    t.text
                  )}
                  {t.tokens && (
                    <div style={{ fontSize: 11, opacity: 0.6, marginTop: 4 }}>
                      in={t.tokens.in}  out={t.tokens.out}  cache_read={t.tokens.cacheRead}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <Space.Compact style={{ width: '100%' }}>
          <Input.TextArea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={status?.configured ? '问 AI 一个关于 ERP 的问题...' : '(AI 未配置，但你可以发，会返回提示)'}
            autoSize={{ minRows: 1, maxRows: 4 }}
            onPressEnter={(e) => {
              if (!e.shiftKey && draft.trim() && !chatMut.isPending) {
                e.preventDefault();
                chatMut.mutate(draft);
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={chatMut.isPending}
            onClick={() => draft.trim() && chatMut.mutate(draft)}
          >
            发送
          </Button>
        </Space.Compact>
      </Card>

      <Alert
        type="info"
        showIcon
        message="安全边界 (plan §7.2)"
        description={
          <ul style={{ marginBottom: 0 }}>
            <li>AI 只给建议，不直接改数据；所有写操作要你确认</li>
            <li>AI 不能修改代码；代码补丁必须经管理员审批 (功能留 Phase 6)</li>
            <li>每次对话都写入 ai_chat_logs 表，可审计回溯</li>
          </ul>
        }
      />
    </Space>
  );
}
