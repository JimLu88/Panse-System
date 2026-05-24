import { useState } from 'react';
import { Alert, Button, Card, FloatButton, Input, List, Spin, Tag, Typography } from 'antd';
import { RobotOutlined, SendOutlined, ThunderboltOutlined, CloseOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { aiChat, reconcileWalkthrough } from '../api/client';

interface Message {
  role: 'user' | 'ai';
  text: string;
}

export default function AiAssistantWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [showWalkthrough, setShowWalkthrough] = useState(false);

  const chatMut = useMutation({
    mutationFn: (msg: string) => aiChat(msg),
    onSuccess: (r) => {
      setMessages(prev => [...prev, { role: 'ai', text: r.text || r.error || '无响应' }]);
    },
  });

  const walkthroughMut = useMutation({
    mutationFn: reconcileWalkthrough,
    onSuccess: () => setShowWalkthrough(true),
  });

  function send() {
    const msg = input.trim();
    if (!msg) return;
    setMessages(prev => [...prev, { role: 'user', text: msg }]);
    setInput('');
    chatMut.mutate(msg);
  }

  return (
    <>
      <FloatButton
        icon={<RobotOutlined />}
        type="primary"
        tooltip="AI 助手"
        style={{ right: 24, bottom: 80 }}
        onClick={() => setOpen(v => !v)}
      />

      {open && (
        <Card
          size="small"
          title={
            <span><RobotOutlined style={{ marginRight: 6 }} />AI 助手</span>
          }
          extra={<Button type="text" icon={<CloseOutlined />} onClick={() => setOpen(false)} />}
          style={{
            position: 'fixed',
            right: 24,
            bottom: 120,
            width: 380,
            maxHeight: 520,
            boxShadow: '0 4px 24px rgba(0,0,0,0.18)',
            zIndex: 1000,
            display: 'flex',
            flexDirection: 'column',
          }}
          bodyStyle={{ padding: 8, display: 'flex', flexDirection: 'column', flex: 1 }}
        >
          {/* 快捷按钮 */}
          <div style={{ marginBottom: 8 }}>
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              loading={walkthroughMut.isPending}
              onClick={() => walkthroughMut.mutate()}
            >
              对账走查
            </Button>
          </div>

          {/* 对账走查结果 */}
          {showWalkthrough && walkthroughMut.data && (
            <Card size="small" style={{ marginBottom: 8, maxHeight: 200, overflow: 'auto' }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                发现 {walkthroughMut.data.total} 条问题
                {walkthroughMut.data.ai_used && <Tag color="blue" style={{ marginLeft: 4 }}>AI 分析</Tag>}
              </Typography.Text>
              <List
                size="small"
                dataSource={walkthroughMut.data.issues.slice(0, 20)}
                renderItem={item => (
                  <List.Item style={{ padding: '4px 0' }}>
                    <div>
                      <Tag color="warning" style={{ fontSize: 11 }}>{item.type}</Tag>
                      <Typography.Text style={{ fontSize: 12 }}>
                        {item.ai_analysis || item.suggestion || item.description}
                      </Typography.Text>
                    </div>
                  </List.Item>
                )}
              />
            </Card>
          )}

          {/* 对话区 */}
          <div style={{ flex: 1, overflow: 'auto', marginBottom: 8, minHeight: 100, maxHeight: 240 }}>
            {messages.length === 0 && (
              <Alert
                type="info"
                showIcon={false}
                message={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  你好！我是 AI 助手，可以帮你定位数据问题、分析对账差异、给出修复建议。
                  也可以点「对账走查」让我扫描全部未解决异常。
                </Typography.Text>}
              />
            )}
            {messages.map((m, i) => (
              <div key={i} style={{
                margin: '4px 0',
                textAlign: m.role === 'user' ? 'right' : 'left',
              }}>
                <span style={{
                  display: 'inline-block',
                  background: m.role === 'user' ? '#1890ff' : '#f5f5f5',
                  color: m.role === 'user' ? '#fff' : '#000',
                  borderRadius: 8,
                  padding: '4px 10px',
                  maxWidth: '85%',
                  fontSize: 13,
                  whiteSpace: 'pre-wrap',
                }}>
                  {m.text}
                </span>
              </div>
            ))}
            {chatMut.isPending && (
              <div style={{ textAlign: 'left', margin: '4px 0' }}>
                <Spin size="small" />
              </div>
            )}
          </div>

          {/* 输入区 */}
          <Input.Search
            value={input}
            onChange={e => setInput(e.target.value)}
            onSearch={send}
            onPressEnter={send}
            placeholder="问我任何问题..."
            enterButton={<SendOutlined />}
            disabled={chatMut.isPending}
          />
        </Card>
      )}
    </>
  );
}
