import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Empty, Input, InputNumber, Select, Space, Spin, Tag, Typography, message,
} from 'antd';
import {
  ArrowLeftOutlined, DeleteOutlined, DownloadOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SaveOutlined,
} from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ProductDimensionAssetSummary, ProductDimensionDetail, getProductDimension, getProductDimensionSvg,
  listProductDimensions, saveProductDimension,
} from '../api/client';

const NS = 'http://www.w3.org/2000/svg';

type Tool = 'select' | 'dimension';
type DimensionRow = { id: string; value: string; source: string; confidence: string };
type Geometry = { x: number; y: number; angle: number };

function localName(node: Element) { return node.tagName.toLowerCase().replace(/^.*:/, ''); }

function sanitizeSvg(doc: Document) {
  doc.querySelectorAll('script, foreignObject, iframe, object, embed').forEach((node) => node.remove());
  doc.querySelectorAll('*').forEach((node) => {
    [...node.attributes].forEach((attr) => {
      if (/^on/i.test(attr.name)) node.removeAttribute(attr.name);
      if (/(?:href|src)$/i.test(attr.name) && attr.value && !attr.value.startsWith('#') && !attr.value.startsWith('data:image/')) {
        node.removeAttribute(attr.name);
      }
    });
  });
}

function textGeometry(text: SVGTextElement): Geometry {
  const match = (text.getAttribute('transform') || '').match(/rotate\(\s*([-\d.]+)/i);
  return {
    x: Number(text.getAttribute('x') || 0),
    y: Number(text.getAttribute('y') || 0),
    angle: Number(text.dataset.angle ?? (match ? match[1] : 0)),
  };
}

function setTextGeometry(text: SVGTextElement, geo: Geometry) {
  text.setAttribute('x', geo.x.toFixed(2));
  text.setAttribute('y', geo.y.toFixed(2));
  text.dataset.angle = String(geo.angle);
  text.setAttribute('transform', `rotate(${geo.angle.toFixed(2)},${geo.x.toFixed(2)},${geo.y.toFixed(2)})`);
}

function svgEl<K extends keyof SVGElementTagNameMap>(name: K, attrs: Record<string, string | number>) {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

export default function ProductDimensionsPage() {
  const { productCode = '' } = useParams();
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const selectedRef = useRef<Element | null>(null);
  const toolRef = useRef<Tool>('select');
  const newDimensionValueRef = useRef('1000');
  const dimensionStartRef = useRef<DOMPoint | null>(null);
  const dragRef = useRef<{ target: Element; start: DOMPoint; dx: number; dy: number } | null>(null);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [assets, setAssets] = useState<ProductDimensionAssetSummary[]>([]);
  const [assetId, setAssetId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProductDimensionDetail | null>(null);
  const [svgText, setSvgText] = useState('');
  const [productName, setProductName] = useState('');
  const [sizeDetail, setSizeDetail] = useState('');
  const [syncSizeDetail, setSyncSizeDetail] = useState(true);
  const [confirmMapping, setConfirmMapping] = useState(false);
  const [tool, setTool] = useState<Tool>('select');
  const [newDimensionValue, setNewDimensionValue] = useState('1000');
  const [rows, setRows] = useState<DimensionRow[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedValue, setSelectedValue] = useState('');
  const [selectedGeo, setSelectedGeo] = useState<Geometry>({ x: 0, y: 0, angle: 0 });
  const [zoom, setZoom] = useState(1);
  const [status, setStatus] = useState('正在载入尺寸图…');

  const labelMeta = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    (detail?.dimension_data?.labels || []).forEach((label) => {
      if (label.id) map.set(String(label.id), label);
    });
    return map;
  }, [detail]);

  const loadCatalog = async (preferred?: number) => {
    setLoading(true);
    try {
      const result = await listProductDimensions(productCode);
      setProductName(result.product.name);
      setAssets(result.assets);
      const next = result.assets.find((item) => item.id === preferred)
        || result.assets.find((item) => item.is_primary)
        || result.assets[0];
      setAssetId(next?.id ?? null);
      if (!next) {
        setDetail(null);
        setSvgText('');
        setSizeDetail(result.product.size_detail || '');
        setStatus('该产品暂时没有尺寸矢量图。');
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取细节尺寸失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadCatalog(); }, [productCode]);

  const loadAsset = async (id: number) => {
    setLoading(true);
    setStatus('正在载入 SVG…');
    try {
      const info = await getProductDimension(productCode, id);
      const svg = await getProductDimensionSvg(productCode, id, info.version);
      setDetail(info);
      setSizeDetail(info.size_detail || '');
      setConfirmMapping(info.mapping_status === 'confirmed');
      setSvgText(svg);
      setStatus(`${info.title} · v${info.version} 已载入，可直接修改。`);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '载入 SVG 失败');
      setStatus('载入失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (assetId != null) void loadAsset(assetId); }, [assetId]);
  useEffect(() => { toolRef.current = tool; dimensionStartRef.current = null; }, [tool]);
  useEffect(() => { newDimensionValueRef.current = newDimensionValue; }, [newDimensionValue]);

  const refreshRows = () => {
    const svg = svgRef.current;
    if (!svg) return;
    const next = [...svg.querySelectorAll<SVGTextElement>('#dimensions-editable text:not([data-panel-static])')]
      .map((text, index) => {
        if (!text.id) text.id = `dim-text-erp-${Date.now()}-${index}`;
        const meta = labelMeta.get(text.id);
        return {
          id: text.id,
          value: text.textContent || '',
          source: String(text.dataset.source || meta?.source || 'visual_label'),
          confidence: String(meta?.confidence || (text.closest('[data-editor-dimension]') ? 'user_confirmed' : 'visual_label')),
        };
      });
    setRows(next);
  };

  const fitCanvas = () => {
    const svg = svgRef.current;
    const scroller = scrollerRef.current;
    if (!svg || !scroller) return;
    const vb = svg.viewBox.baseVal;
    const next = Math.max(.12, Math.min(1.5, (scroller.clientWidth - 64) / vb.width, (scroller.clientHeight - 64) / vb.height));
    setZoom(next);
  };

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const vb = svg.viewBox.baseVal;
    svg.style.width = `${Math.round(vb.width * zoom)}px`;
    svg.style.height = `${Math.round(vb.height * zoom)}px`;
  }, [zoom, svgText]);

  const selectElement = (element: Element | null) => {
    selectedRef.current?.classList.remove('erp-dimension-selected');
    selectedRef.current = element;
    element?.classList.add('erp-dimension-selected');
    setSelectedId(element?.id || element?.querySelector('text')?.id || null);
    const text = element && (localName(element) === 'text' ? element : element.querySelector('text')) as SVGTextElement | null;
    if (text) {
      setSelectedValue(text.textContent || '');
      setSelectedGeo(textGeometry(text));
    }
  };

  const selectedTextNode = () => {
    const selected = selectedRef.current;
    if (!selected) return null;
    return (localName(selected) === 'text' ? selected : selected.querySelector('text')) as SVGTextElement | null;
  };

  useEffect(() => {
    if (!svgText || !canvasRef.current) return;
    const parsed = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    if (parsed.querySelector('parsererror')) {
      setStatus('SVG 解析失败');
      return;
    }
    sanitizeSvg(parsed);
    const imported = document.importNode(parsed.documentElement, true) as unknown as SVGSVGElement;
    imported.removeAttribute('width');
    imported.removeAttribute('height');
    imported.style.display = 'block';
    imported.style.background = '#fff';
    canvasRef.current.replaceChildren(imported);
    svgRef.current = imported;
    selectedRef.current = null;
    setSelectedId(null);

    const pointOf = (event: PointerEvent) => {
      const point = imported.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      return point.matrixTransform(imported.getScreenCTM()!.inverse());
    };
    const moveElement = (element: Element, dx: number, dy: number) => {
      if (localName(element) === 'text') {
        const text = element as SVGTextElement;
        const geo = textGeometry(text);
        setTextGeometry(text, { ...geo, x: geo.x + dx, y: geo.y + dy });
        return;
      }
      const html = element as HTMLElement;
      const tx = Number(html.dataset.editorX || 0) + dx;
      const ty = Number(html.dataset.editorY || 0) + dy;
      if (html.dataset.baseTransform == null) html.dataset.baseTransform = element.getAttribute('transform') || '';
      html.dataset.editorX = String(tx);
      html.dataset.editorY = String(ty);
      element.setAttribute('transform', `translate(${tx.toFixed(2)} ${ty.toFixed(2)}) ${html.dataset.baseTransform}`.trim());
    };
    const addDimension = (start: DOMPoint, end: DOMPoint) => {
      const vb = imported.viewBox.baseVal;
      const diag = Math.hypot(vb.width, vb.height);
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const length = Math.hypot(dx, dy);
      if (length < diag * .012) { setStatus('两点太近，请重新选择。'); return; }
      const ux = dx / length; const uy = dy / length;
      const px = -uy; const py = ux;
      const tick = diag * .011; const offset = diag * .018; const stroke = Math.max(1, diag * .0012);
      const stamp = Date.now();
      const group = svgEl('g', { id: `dimension-manual-${stamp}`, 'data-editor-dimension': 'manual' });
      group.append(
        svgEl('line', { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: '#292724', 'stroke-width': stroke, 'vector-effect': 'non-scaling-stroke' }),
        svgEl('line', { x1: start.x - px * tick, y1: start.y - py * tick, x2: start.x + px * tick, y2: start.y + py * tick, stroke: '#292724', 'stroke-width': stroke, 'vector-effect': 'non-scaling-stroke' }),
        svgEl('line', { x1: end.x - px * tick, y1: end.y - py * tick, x2: end.x + px * tick, y2: end.y + py * tick, stroke: '#292724', 'stroke-width': stroke, 'vector-effect': 'non-scaling-stroke' }),
      );
      let angle = Math.atan2(dy, dx) * 180 / Math.PI;
      if (angle > 90 || angle < -90) angle += 180;
      const text = svgEl('text', {
        id: `dim-text-manual-${stamp}`, 'text-anchor': 'middle', 'dominant-baseline': 'central',
        'font-family': 'Microsoft YaHei, sans-serif', 'font-size': Math.max(16, diag * .018),
        'font-weight': 700, fill: '#292724', 'data-source': 'erp.manual_dimension', 'data-editor-dimension': 'manual',
      });
      text.textContent = newDimensionValueRef.current.trim() || '1000';
      setTextGeometry(text, { x: (start.x + end.x) / 2 + px * offset, y: (start.y + end.y) / 2 + py * offset, angle });
      group.appendChild(text);
      let dimensions = imported.querySelector('#dimensions-editable');
      if (!dimensions) {
        dimensions = svgEl('g', { id: 'dimensions-editable', 'data-layer-name': '尺寸-可编辑' });
        imported.appendChild(dimensions);
      }
      dimensions.appendChild(group);
      selectElement(text);
      refreshRows();
      setTool('select');
      setStatus('新尺寸已加入；保存后同步到 ERP。');
    };
    const onDown = (event: PointerEvent) => {
      if (toolRef.current === 'dimension') {
        const point = pointOf(event);
        if (!dimensionStartRef.current) {
          dimensionStartRef.current = point;
          setStatus('已选起点，请点击尺寸线终点。');
        } else {
          const start = dimensionStartRef.current;
          dimensionStartRef.current = null;
          addDimension(start, point);
        }
        event.preventDefault();
        return;
      }
      const raw = event.target as Element;
      const target = raw.closest('[data-editor-dimension], #dimensions-editable text:not([data-panel-static]), #dimensions-editable path:not([data-panel-static]), #dimensions-editable line:not([data-panel-static])');
      selectElement(target);
      if (!target) return;
      dragRef.current = { target, start: pointOf(event), dx: 0, dy: 0 };
      event.preventDefault();
    };
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const point = pointOf(event);
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      moveElement(drag.target, dx - drag.dx, dy - drag.dy);
      drag.dx = dx; drag.dy = dy;
      const text = selectedTextNode();
      if (text) setSelectedGeo(textGeometry(text));
    };
    const onUp = () => { if (dragRef.current) { dragRef.current = null; refreshRows(); } };
    imported.addEventListener('pointerdown', onDown);
    imported.addEventListener('pointermove', onMove);
    imported.addEventListener('pointerup', onUp);
    imported.addEventListener('pointercancel', onUp);
    refreshRows();
    requestAnimationFrame(fitCanvas);
    return () => {
      imported.removeEventListener('pointerdown', onDown);
      imported.removeEventListener('pointermove', onMove);
      imported.removeEventListener('pointerup', onUp);
      imported.removeEventListener('pointercancel', onUp);
    };
  }, [svgText, labelMeta]);

  const updateRow = (id: string, value: string) => {
    const text = svgRef.current?.querySelector<SVGTextElement>(`#${CSS.escape(id)}`);
    if (!text) return;
    text.textContent = value;
    if (selectedTextNode() === text) setSelectedValue(value);
    setRows((current) => current.map((row) => row.id === id ? { ...row, value } : row));
  };

  const applySelection = (value: string, geo: Geometry) => {
    const text = selectedTextNode();
    if (!text) return;
    text.textContent = value;
    setTextGeometry(text, geo);
    setSelectedValue(value);
    setSelectedGeo(geo);
    refreshRows();
  };

  const deleteSelected = () => {
    const selected = selectedRef.current;
    if (!selected) return;
    const target = selected.closest('[data-editor-dimension]') || selected;
    selectElement(null);
    target.remove();
    refreshRows();
    setStatus('标注已删除；点击保存后生效。');
  };

  const serializeSvg = () => {
    const svg = svgRef.current;
    if (!svg) return '';
    const clone = svg.cloneNode(true) as SVGSVGElement;
    clone.classList.remove('dimension-tool');
    clone.querySelectorAll('.erp-dimension-selected').forEach((node) => node.classList.remove('erp-dimension-selected'));
    clone.removeAttribute('style');
    clone.setAttribute('xmlns', NS);
    clone.querySelectorAll('*').forEach((node) => {
      [...node.attributes].forEach((attr) => {
        if (attr.name.startsWith('data-editor-') && attr.name !== 'data-editor-dimension') node.removeAttribute(attr.name);
      });
    });
    return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(clone)}\n`;
  };

  const save = async () => {
    if (!detail) return;
    if (detail.mapping_status === 'review_required' && !confirmMapping) {
      message.warning('请先确认这张图确实属于当前产品');
      return;
    }
    setSaving(true);
    try {
      const saved = await saveProductDimension(productCode, detail.id, {
        svg: serializeSvg(), expected_version: detail.version, size_detail: sizeDetail || null,
        sync_size_detail: syncSizeDetail, confirm_mapping: confirmMapping,
      });
      setDetail(saved);
      setConfirmMapping(saved.mapping_status === 'confirmed');
      setStatus(`保存成功 · 当前 v${saved.version}${saved.backup ? ' · 原版本已自动备份' : ''}`);
      message.success('细节尺寸和 ERP 数据已保存');
      await loadCatalog(saved.id);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const download = () => {
    const blob = new Blob([serializeSvg()], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${detail?.title || productCode}.svg`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  };

  const sourceLabel = (source: string) => {
    if (source === 'erp.size_detail') return <Tag color="green">ERP 已确认</Tag>;
    if (source === 'erp.sku_name') return <Tag color="blue">SKU 规格</Tag>;
    if (source === 'erp.user_edit' || source === 'erp.manual_dimension') return <Tag color="cyan">人工修改</Tag>;
    if (source.includes('proportion') || source.includes('estimate')) return <Tag color="orange">约 / 待复核</Tag>;
    return <Tag>原图标注</Tag>;
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <style>{`
        .dimension-canvas svg { user-select:none; touch-action:none; }
        .dimension-canvas .erp-dimension-selected { filter: drop-shadow(0 0 4px #1677ff); outline: 3px solid #1677ff; }
        .dimension-row-active { background:#e6f4ff !important; border-color:#91caff !important; }
        @media (max-width: 900px) { .dimension-editor-grid { grid-template-columns:1fr !important; } }
      `}</style>
      <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/products')}>返回产品表</Button>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>细节尺寸 · {productName || productCode}</Typography.Title>
            <Typography.Text type="secondary">{productCode}</Typography.Text>
          </div>
        </Space>
        <Space wrap>
          <Select
            style={{ minWidth: 240 }} value={assetId ?? undefined} onChange={setAssetId}
            options={assets.map((asset) => ({
              value: asset.id,
              label: `${asset.title} · ${asset.dimension_count}项${asset.mapping_status === 'review_required' ? ' · 待核对' : ''}`,
            }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => assetId && loadAsset(assetId)}>重新载入</Button>
          <Button icon={<DownloadOutlined />} disabled={!detail} onClick={download}>下载 SVG</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!detail} onClick={save}>保存并同步 ERP</Button>
        </Space>
      </Space>

      {loading && !detail ? <div style={{ padding: 60, textAlign: 'center' }}><Spin /></div> : assets.length === 0 ? (
        <Card><Empty description="该产品暂时没有细节尺寸图；39 份旧 PSD 只会绑定到已匹配产品。" /></Card>
      ) : detail && (
        <>
          {detail.mapping_status === 'review_required' && (
            <Alert type="warning" showIcon message="这张图与 ERP 产品的名称映射为候选关系"
              description={<Checkbox checked={confirmMapping} onChange={(e) => setConfirmMapping(e.target.checked)}>我已核对：这张图确实属于 {productName}</Checkbox>} />
          )}
          <div className="dimension-editor-grid" style={{ display: 'grid', gridTemplateColumns: '360px minmax(0, 1fr)', gap: 16 }}>
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Card size="small" title="标注工具">
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space.Compact block>
                    <Button type={tool === 'select' ? 'primary' : 'default'} icon={<EditOutlined />} onClick={() => setTool('select')}>选择 / 拖动</Button>
                    <Button type={tool === 'dimension' ? 'primary' : 'default'} icon={<PlusOutlined />} onClick={() => setTool('dimension')}>新增尺寸线</Button>
                  </Space.Compact>
                  <Input addonBefore="新尺寸值" value={newDimensionValue} onChange={(e) => setNewDimensionValue(e.target.value)} />
                  <Typography.Text type="secondary">新增时依次点击起点、终点；文字和尺寸线可拖动。</Typography.Text>
                </Space>
              </Card>

              <Card size="small" title="当前选中" extra={selectedId && <Button danger size="small" icon={<DeleteOutlined />} onClick={deleteSelected}>删除</Button>}>
                {!selectedId ? <Typography.Text type="secondary">点击画布中的尺寸文字或线条</Typography.Text> : (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Input value={selectedValue} onChange={(e) => applySelection(e.target.value, selectedGeo)} />
                    <Space wrap>
                      <InputNumber addonBefore="X" value={selectedGeo.x} onChange={(v) => applySelection(selectedValue, { ...selectedGeo, x: Number(v || 0) })} />
                      <InputNumber addonBefore="Y" value={selectedGeo.y} onChange={(v) => applySelection(selectedValue, { ...selectedGeo, y: Number(v || 0) })} />
                      <InputNumber addonBefore="角度" value={selectedGeo.angle} onChange={(v) => applySelection(selectedValue, { ...selectedGeo, angle: Number(v || 0) })} />
                    </Space>
                  </Space>
                )}
              </Card>

              <Card size="small" title={`已有尺寸（${rows.length}）`} styles={{ body: { maxHeight: 370, overflow: 'auto', padding: 10 } }}>
                <Space direction="vertical" size={6} style={{ width: '100%' }}>
                  {rows.map((row, index) => (
                    <div key={row.id} className={selectedId === row.id ? 'dimension-row-active' : ''}
                      style={{ border: '1px solid #eee', borderRadius: 6, padding: 6, cursor: 'pointer' }}
                      onClick={() => selectElement(svgRef.current?.querySelector(`#${CSS.escape(row.id)}`) || null)}>
                      <Space.Compact block>
                        <Button tabIndex={-1} style={{ width: 42 }}>{String(index + 1).padStart(2, '0')}</Button>
                        <Input value={row.value} onChange={(e) => updateRow(row.id, e.target.value)} onClick={(e) => e.stopPropagation()} />
                      </Space.Compact>
                      <div style={{ marginTop: 5 }}>{sourceLabel(row.source)}</div>
                    </div>
                  ))}
                </Space>
              </Card>

              <Card size="small" title="ERP 产品尺寸明细">
                <Input.TextArea rows={5} value={sizeDetail} onChange={(e) => setSizeDetail(e.target.value)}
                  placeholder={'例：总长：1800mm\n总深：450mm\n内部净高：320mm'} />
                <Checkbox style={{ marginTop: 10 }} checked={syncSizeDetail} onChange={(e) => setSyncSizeDetail(e.target.checked)}>
                  保存时同步到产品档案的“尺寸明细”
                </Checkbox>
                <Alert style={{ marginTop: 10 }} type="info" showIcon message="图上所有标注都会写入结构化尺寸记录；只有这个文本框会覆盖产品总表的通用尺寸明细。" />
              </Card>

              <Card size="small" title={`ERP SKU 规格（${detail.sku_variants.length}）`} styles={{ body: { maxHeight: 300, overflow: 'auto' } }}>
                {detail.sku_variants.length === 0 ? <Typography.Text type="secondary">暂无 SKU 规格</Typography.Text> : detail.sku_variants.map((variant, index) => {
                  const dimensions = variant.resolved_dimensions || variant.measurements || [];
                  return <div key={variant.sku_code || index} style={{ marginBottom: 9, paddingBottom: 9, borderBottom: '1px solid #f0f0f0' }}>
                    <Typography.Text strong>{variant.name || variant.sku_code}</Typography.Text>
                    <div><Typography.Text type="secondary">{dimensions.map((d) => `${d.label || '尺寸'} ${d.value_mm ?? '-'}mm`).join(' / ') || '规格名没有明确尺寸'}</Typography.Text></div>
                  </div>;
                })}
              </Card>
            </Space>

            <Card styles={{ body: { padding: 0 } }}>
              <div style={{ height: 48, padding: '8px 12px', borderBottom: '1px solid #eee', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Button size="small" onClick={() => setZoom((v) => Math.max(.1, v / 1.2))}>－</Button>
                <Button size="small" onClick={fitCanvas}>适应画布</Button>
                <Button size="small" onClick={() => setZoom((v) => Math.min(4, v * 1.2))}>＋</Button>
                <Tag>{Math.round(zoom * 100)}%</Tag>
                <span style={{ flex: 1 }} />
                {detail.mapping_status === 'review_required' ? <Tag color="orange">待核对映射</Tag> : <Tag color="green">已绑定 ERP</Tag>}
                <Tag color="blue">v{detail.version}</Tag>
              </div>
              <div ref={scrollerRef} style={{ height: 'calc(100vh - 260px)', minHeight: 620, overflow: 'auto', background: '#e7e8ea', padding: 28 }}>
                <div ref={canvasRef} className="dimension-canvas" style={{ width: 'max-content', margin: '0 auto', boxShadow: '0 8px 32px rgba(0,0,0,.16)' }} />
              </div>
              <div style={{ minHeight: 36, padding: '8px 12px', borderTop: '1px solid #eee' }}><Typography.Text type="secondary">{status}</Typography.Text></div>
            </Card>
          </div>
        </>
      )}
    </Space>
  );
}
