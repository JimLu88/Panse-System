import { useEffect, useState } from 'react';
import { Card, Input, Button, Tag, message, Space, Typography, List } from 'antd';
import {
  aiSuggestNpd, listNpdKnowledgeNotes, addNpdKnowledgeNote,
  type NpdKnowledgeNote, type NpdAiSuggest,
} from '../api/client';

export default function NpdKnowledgePanel() {
  const [q, setQ] = useState('');
  const [cat, setCat] = useState('');
  const [mat, setMat] = useState('');
  const [asking, setAsking] = useState(false);
  const [ans, setAns] = useState<NpdAiSuggest | null>(null);

  const ask = async () => {
    if (!q.trim()) { message.warning('输入问题'); return; }
    setAsking(true);
    try {
      setAns(await aiSuggestNpd({ question: q.trim(), category: cat || null, material: mat || null }));
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '失败');
    } finally {
      setAsking(false);
    }
  };

  const [notes, setNotes] = useState<NpdKnowledgeNote[]>([]);
  const [nq, setNq] = useState('');
  const loadNotes = async () => {
    try { setNotes(await listNpdKnowledgeNotes(nq || undefined)); } catch { /* ignore */ }
  };
  useEffect(() => { loadNotes(); /* eslint-disable-next-line */ }, []);

  const [nTitle, setNTitle] = useState('');
  const [nCat, setNCat] = useState('');
  const [nMat, setNMat] = useState('');
  const [nBody, setNBody] = useState('');
  const addNote = async () => {
    if (!nTitle.trim()) { message.warning('填标题'); return; }
    try {
      await addNpdKnowledgeNote({ title: nTitle.trim(), category: nCat || null, material: nMat || null, body: nBody || null });
      setNTitle(''); setNCat(''); setNMat(''); setNBody('');
      message.success('已存'); loadNotes();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? '失败');
    }
  };

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={16}>
      <Card size="small" title="AI 设计助手 (材质/工艺/设计边界/询价话术)">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea rows={2} value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="如: 樱桃木做岩板餐桌脚要注意什么 / 帮我写一段岩板台面的供应商询价话术" />
          <Space wrap>
            <Input placeholder="品类(可选)" value={cat} onChange={(e) => setCat(e.target.value)} style={{ width: 130 }} />
            <Input placeholder="材质(可选)" value={mat} onChange={(e) => setMat(e.target.value)} style={{ width: 130 }} />
            <Button type="primary" loading={asking} onClick={ask}>问 AI</Button>
          </Space>
          {ans && (
            <div>
              {ans.suggestion
                ? <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{ans.suggestion}</Typography.Paragraph>
                : <Typography.Text type="warning">{ans.note}</Typography.Text>}
              {ans.sources && ans.sources.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>本库依据: </Typography.Text>
                  {ans.sources.map((s, i) => (
                    <Tag key={i}>{s.type === 'material' ? '物料' : s.type === 'note' ? '笔记' : '工艺'}: {s.name}</Tag>
                  ))}
                </div>
              )}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                ※ AI 建议仅供参考;检测数值/价格/合规以实测、实报、真凭证为准。
              </Typography.Text>
            </div>
          )}
        </Space>
      </Card>

      <Card size="small" title="知识库笔记"
        extra={(
          <Space>
            <Input placeholder="搜索" value={nq} size="small" style={{ width: 140 }}
              onChange={(e) => setNq(e.target.value)} onPressEnter={loadNotes} />
            <Button size="small" onClick={loadNotes}>搜</Button>
          </Space>
        )}>
        <Space wrap style={{ marginBottom: 8 }}>
          <Input placeholder="标题" value={nTitle} onChange={(e) => setNTitle(e.target.value)} style={{ width: 160 }} />
          <Input placeholder="品类" value={nCat} onChange={(e) => setNCat(e.target.value)} style={{ width: 90 }} />
          <Input placeholder="材质" value={nMat} onChange={(e) => setNMat(e.target.value)} style={{ width: 90 }} />
          <Input placeholder="内容/经验" value={nBody} onChange={(e) => setNBody(e.target.value)} style={{ width: 240 }} />
          <Button onClick={addNote}>存笔记</Button>
        </Space>
        <List size="small" dataSource={notes} locale={{ emptyText: '暂无笔记' }}
          renderItem={(n) => (
            <List.Item>
              <List.Item.Meta
                title={<span>{n.title} {n.category && <Tag>{n.category}</Tag>}{n.material && <Tag color="blue">{n.material}</Tag>}</span>}
                description={n.body} />
            </List.Item>
          )} />
      </Card>
    </Space>
  );
}
