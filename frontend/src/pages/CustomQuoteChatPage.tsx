import { useState, useRef } from 'react';
import {
  Alert,
  Button,
  Card,
  Image,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import {
  ClearOutlined,
  DeleteOutlined,
  PictureOutlined,
  RobotOutlined,
  SendOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { CUTE_IMG } from '../components/ProductThumb';

const { TextArea } = Input;
const { Text, Paragraph } = Typography;

interface ChatResult {
  text: string;
  route_type: 'full_custom' | 'micro_custom' | 'unknown';
  suggested_sku?: string;
  model: string;
  ai_used: boolean;
  error?: string;
}

interface ImagePreview {
  uid: string;
  file: File;
  url: string;
}

const MAX_IMAGES = 5;

function formatText(text: string) {
  // Bold section headers like 【xxx】
  const parts = text.split(/(【[^】]+】)/g);
  return parts.map((p, i) => {
    if (p.startsWith('【') && p.endsWith('】')) {
      return <Text key={i} strong style={{ color: '#1677ff' }}>{p}</Text>;
    }
    return <span key={i}>{p}</span>;
  });
}

export default function CustomQuoteChatPage() {
  const nav = useNavigate();
  const [userMsg, setUserMsg] = useState('');
  const [images, setImages] = useState<ImagePreview[]>([]);
  const [modelPref, setModelPref] = useState<'sonnet' | 'opus'>('sonnet');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ChatResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addImages = (files: File[]) => {
    const remaining = MAX_IMAGES - images.length;
    const toAdd = files.slice(0, remaining);
    if (files.length > remaining) {
      message.warning(`最多上传 ${MAX_IMAGES} 张图片，已截取前 ${remaining} 张`);
    }
    const previews: ImagePreview[] = toAdd.map((f) => ({
      uid: `${Date.now()}-${Math.random()}`,
      file: f,
      url: URL.createObjectURL(f),
    }));
    setImages((prev) => [...prev, ...previews]);
  };

  const removeImage = (uid: string) => {
    setImages((prev) => {
      const img = prev.find((i) => i.uid === uid);
      if (img) URL.revokeObjectURL(img.url);
      return prev.filter((i) => i.uid !== uid);
    });
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith('image/'));
    if (files.length > 0) {
      e.preventDefault();
      addImages(files);
    }
  };

  const handleSend = async () => {
    if (!userMsg.trim() && images.length === 0) {
      message.warning('请输入描述或上传图片');
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const fd = new FormData();
      fd.append('message', userMsg.trim());
      fd.append('model_pref', modelPref);
      for (const img of images) {
        fd.append('images', img.file, img.file.name);
      }
      const token = localStorage.getItem('panse_token');
      const resp = await fetch('/api/customization/ai-chat', {
        method: 'POST',
        body: fd,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: '请求失败' }));
        throw new Error(err.detail ?? '请求失败');
      }
      const data: ChatResult = await resp.json();
      setResult(data);
    } catch (e: any) {
      message.error(e.message ?? '分析失败');
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setUserMsg('');
    images.forEach((i) => URL.revokeObjectURL(i.url));
    setImages([]);
    setResult(null);
  };

  const routeLabel = result?.route_type === 'micro_custom'
    ? { label: '微定制（已有产品改尺寸/材质）', color: 'blue' }
    : result?.route_type === 'full_custom'
    ? { label: '全定制（全新产品）', color: 'orange' }
    : null;

  return (
    <Space direction="vertical" style={{ width: '100%', maxWidth: 900 }} size="middle">
      <Space align="center">
        <Typography.Title level={4} style={{ margin: 0 }}>
          定制报价
        </Typography.Title>
        <Tag color="purple" icon={<RobotOutlined />}>AI 对话</Tag>
      </Space>

      <Alert
        type="info"
        showIcon
        message="描述您的定制需求（文字 + 可选图片），AI 将自动判断是全定制还是已有产品微改，并给出报价方向和下一步操作建议。图片支持粘贴或拖入。"
      />

      <Card size="small" title="描述定制需求">
        <Space direction="vertical" style={{ width: '100%' }} size={8}>
          <TextArea
            value={userMsg}
            onChange={(e) => setUserMsg(e.target.value)}
            onPaste={handlePaste}
            placeholder="例如：客户需要一张2.2米长的黑胡桃餐桌，腿部需要改成锥形，宽度90cm高度75cm..."
            autoSize={{ minRows: 4, maxRows: 10 }}
            disabled={loading}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSend();
            }}
          />

          {/* Image previews */}
          {images.length > 0 && (
            <Space wrap size={8}>
              {images.map((img) => (
                <div key={img.uid} style={{ position: 'relative', display: 'inline-block' }}>
                  <Image
                    src={img.url}
                    width={80}
                    height={80}
                    style={{ objectFit: 'cover', borderRadius: 4, border: '1px solid #d9d9d9' }}
                    preview={false}
                    fallback={CUTE_IMG}
                  />
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    style={{
                      position: 'absolute', top: -8, right: -8,
                      width: 20, height: 20, minWidth: 20, padding: 0,
                      borderRadius: '50%', fontSize: 10,
                    }}
                    onClick={() => removeImage(img.uid)}
                  />
                </div>
              ))}
              {images.length < MAX_IMAGES && (
                <Button
                  icon={<PictureOutlined />}
                  style={{ width: 80, height: 80 }}
                  onClick={() => fileInputRef.current?.click()}
                >
                  +图片
                </Button>
              )}
            </Space>
          )}

          <Space wrap>
            {images.length === 0 && (
              <Button
                icon={<PictureOutlined />}
                onClick={() => fileInputRef.current?.click()}
                disabled={loading}
              >
                上传图片（最多5张）
              </Button>
            )}

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: 'none' }}
              onChange={(e) => {
                if (e.target.files) addImages(Array.from(e.target.files));
                e.target.value = '';
              }}
            />

            <Button
              icon={modelPref === 'opus' ? <ThunderboltOutlined /> : <RobotOutlined />}
              type={modelPref === 'opus' ? 'primary' : 'default'}
              onClick={() => setModelPref((p) => p === 'opus' ? 'sonnet' : 'opus')}
              disabled={loading}
              title={modelPref === 'opus' ? '当前: Opus（慢但更准）- 点击切换回 Sonnet' : '点击切换到 Opus（更强大）'}
            >
              {modelPref === 'opus' ? 'Opus 模式' : 'Sonnet 模式'}
            </Button>

            <Button onClick={handleClear} icon={<ClearOutlined />} disabled={loading}>
              清空
            </Button>

            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={loading}
              onClick={handleSend}
            >
              分析定制需求 {!loading && <Text style={{ color: 'rgba(255,255,255,0.7)', fontSize: 11, marginLeft: 4 }}>Ctrl+Enter</Text>}
            </Button>
          </Space>
        </Space>
      </Card>

      {loading && (
        <Card size="small">
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <Spin tip={`${modelPref === 'opus' ? 'Opus' : 'Sonnet'} 分析中，请稍候...`}>
              <div style={{ minHeight: 40 }} />
            </Spin>
          </div>
        </Card>
      )}

      {result && !loading && (
        <Card
          size="small"
          title={
            <Space>
              <span>AI 分析结果</span>
              {result.ai_used && <Tag color="blue" icon={<RobotOutlined />}>AI 已分析</Tag>}
              {routeLabel && <Tag color={routeLabel.color}>{routeLabel.label}</Tag>}
              <Tag color="default" style={{ fontSize: 11 }}>{result.model}</Tag>
            </Space>
          }
          extra={
            <Space>
              {result.route_type === 'micro_custom' && (
                <Button
                  type="primary"
                  size="small"
                  onClick={() => {
                    const qs = result.suggested_sku ? `?sku=${result.suggested_sku}` : '';
                    nav(`/customization${qs}`);
                  }}
                >
                  → 进入微定制向导
                </Button>
              )}
              {result.route_type === 'full_custom' && (
                <Button
                  type="primary"
                  size="small"
                  onClick={() => nav('/customization?tab=full')}
                >
                  → 进入全定制报价
                </Button>
              )}
            </Space>
          }
        >
          {result.error && !result.ai_used && (
            <Alert type="warning" message={result.error} style={{ marginBottom: 12 }} />
          )}

          <Paragraph style={{ whiteSpace: 'pre-line', lineHeight: 1.8 }}>
            {formatText(result.text)}
          </Paragraph>

          {result.suggested_sku && (
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">建议基础 SKU：</Text>
              <Tag color="cyan" style={{ marginLeft: 8, fontSize: 13 }}>{result.suggested_sku}</Tag>
            </div>
          )}
        </Card>
      )}

      {/* Quick links to sub-tools */}
      <Card size="small" title="其他定制工具" bodyStyle={{ paddingTop: 8 }}>
        <Space wrap>
          <Button size="small" onClick={() => nav('/customization?tab=full')}>全定制板单报价</Button>
          <Button size="small" onClick={() => nav('/customization?tab=ai')}>AI 截图报价</Button>
          <Button size="small" onClick={() => nav('/customization?tab=manual')}>手动定制向导</Button>
          <Button size="small" onClick={() => nav('/customization?tab=competitor')}>竞品价库</Button>
          <Button size="small" onClick={() => nav('/customization?tab=settings')}>报价参数设置</Button>
        </Space>
      </Card>
    </Space>
  );
}
