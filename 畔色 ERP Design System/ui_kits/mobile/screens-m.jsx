// 移动端屏幕集合

// 工作台
function HomeM({ go }) {
  const todos = [
    ['待发货订单', '12 单', 'warning'], ['待对账流水', '3 笔', 'warning'],
    ['缺料预警', '5 种', 'danger'], ['今日已签收', '28 单', 'success'],
  ];
  const actions = [
    ['photo_camera', '拍照录单', 'capture'], ['search', '库存查询', 'stock'],
    ['add_circle', '新建订单', null], ['credit_card', '对账核销', null],
  ];
  return (
    <div>
      <div style={{ background: 'linear-gradient(160deg,var(--teal-700),var(--teal-900))', padding: '18px 18px 26px', color: '#fff' }}>
        <div style={{ fontSize: 13, opacity: .8 }}>上午好，管理员 👋</div>
        <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: '-.02em', marginTop: 2 }}>畔色孚格 ERP</div>
        <div className="m-card" style={{ marginTop: 16, background: 'rgba(255,255,255,.12)', border: '1px solid rgba(255,255,255,.18)', backdropFilter: 'blur(6px)' }}>
          <div style={{ padding: 14, display: 'flex', justifyContent: 'space-between' }}>
            <div><div style={{ fontSize: 12, opacity: .8 }}>今日订单</div><div className="m-mono" style={{ fontSize: 26, fontWeight: 800 }}>36</div></div>
            <div style={{ textAlign: 'right' }}><div style={{ fontSize: 12, opacity: .8 }}>今日收入</div><div className="m-mono" style={{ fontSize: 26, fontWeight: 800 }}>¥184k</div></div>
          </div>
        </div>
      </div>
      <div className="m-pad" style={{ marginTop: -12 }}>
        <div className="m-card" style={{ padding: 14 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 6 }}>
            {actions.map(([ic, label, dst]) => (
              <div key={label} onClick={() => dst && go(dst)} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7, padding: '8px 0', cursor: 'pointer' }}>
                <div style={{ width: 46, height: 46, borderRadius: 14, background: 'var(--primary-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><span className="material-symbols-outlined" style={{ fontSize: 24, color: 'var(--primary)' }}>{ic}</span></div>
                <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>{label}</span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '20px 2px 10px' }}>待办事项</div>
        <div className="m-card" style={{ overflow: 'hidden' }}>
          {todos.map(([label, val, tone]) => (
            <div key={label} className="m-row">
              <div style={{ flex: 1, fontSize: 15, color: 'var(--text-primary)' }}>{label}</div>
              <MTag tone={tone} dot>{val}</MTag>
              <span style={{ color: 'var(--text-tertiary)' }}>›</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// 拍照录单
function CaptureM() {
  const [stage, setStage] = React.useState('shoot'); // shoot → recognizing → result
  React.useEffect(() => { if (stage === 'recognizing') { const t = setTimeout(() => setStage('result'), 1400); return () => clearTimeout(t); } }, [stage]);
  return (
    <div>
      <div className="m-hd"><h1 className="m-h1">拍照录单</h1><div style={{ color: 'var(--text-secondary)', fontSize: 13, marginTop: 4 }}>拍送货单 / 账单，AI 自动识别入库</div></div>
      <div className="m-pad">
        {stage === 'shoot' && (
          <div>
            <div style={{ border: '2px dashed var(--border-strong)', borderRadius: 'var(--radius-lg)', height: 280, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, background: 'var(--surface-sunken)', color: 'var(--text-tertiary)' }}>
              <div style={{ fontSize: 48, color: 'var(--text-tertiary)' }}><span className="material-symbols-outlined" style={{ fontSize: 52 }}>document_scanner</span></div>
              <div style={{ fontSize: 14 }}>将送货单放入取景框</div>
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button className="m-btn m-btn--sec" style={{ flex: 1 }}>从相册选</button>
              <button className="m-btn" style={{ flex: 1 }} onClick={() => setStage('recognizing')}>📷 拍照识别</button>
            </div>
          </div>
        )}
        {stage === 'recognizing' && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: 'var(--text-secondary)' }}>
            <div style={{ width: 44, height: 44, border: '3px solid var(--primary-soft)', borderTopColor: 'var(--primary)', borderRadius: '50%', margin: '0 auto 18px', animation: 'mspin .7s linear infinite' }} />
            <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>AI 识别中…</div>
            <div style={{ fontSize: 13, marginTop: 4 }}>正在解析单据字段</div>
          </div>
        )}
        {stage === 'result' && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <MTag tone="success" dot>识别完成</MTag>
              <span style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>置信度 96%，请核对</span>
            </div>
            <div className="m-card" style={{ overflow: 'hidden' }}>
              {[['供应商', '佳宝家居厂'], ['送货单号', 'SH-20260622-07'], ['日期', '2026-06-22'], ['金额', '¥31,900'], ['件数', '48 件']].map(([k, v]) => (
                <div key={k} className="m-row"><span style={{ width: 80, color: 'var(--text-tertiary)', fontSize: 13 }}>{k}</span><span className={`${k === '金额' || k.includes('号') ? 'm-mono' : ''}`} style={{ flex: 1, fontWeight: 500, color: 'var(--text-primary)' }}>{v}</span><span style={{ color: 'var(--primary)', fontSize: 13 }}>编辑</span></div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button className="m-btn m-btn--sec" style={{ flex: 1 }} onClick={() => setStage('shoot')}>重拍</button>
              <button className="m-btn" style={{ flex: 1.4 }} onClick={() => setStage('shoot')}>确认入库</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// 库存查询
function StockM() {
  const items = [
    ['橡木板材 18mm', 'WD-OAK-001', '1,280 张', 'success', '充足'],
    ['阻尼铰链', 'HW-HINGE-22', '96 只', 'warning', '缺料'],
    ['松木方料 40×40', 'WD-PINE-014', '−24 根', 'danger', '负库存'],
    ['亚麻布料 米白', 'FB-LINEN-07', '540 米', 'success', '充足'],
    ['三节导轨 450mm', 'HW-RAIL-33', '220 套', 'warning', '缺料'],
    ['加固纸箱 XL', 'PK-BOX-XL', '1,840 个', 'success', '充足'],
  ];
  return (
    <div>
      <div className="m-hd"><h1 className="m-h1">库存查询</h1></div>
      <div className="m-pad">
        <div className="m-input" style={{ marginBottom: 14 }}><span>🔍</span><input placeholder="搜索物料名称 / 编号" /></div>
        <div className="m-card" style={{ overflow: 'hidden' }}>
          {items.map(([name, code, qty, tone, st]) => (
            <div key={code} className="m-row">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 500, color: 'var(--text-primary)' }}>{name}</div>
                <div className="m-mono" style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>{code}</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div className="m-mono" style={{ fontSize: 15, fontWeight: 600, color: tone === 'danger' ? 'var(--danger)' : 'var(--text-primary)' }}>{qty}</div>
                <div style={{ marginTop: 4 }}><MTag tone={tone} dot>{st}</MTag></div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// 我的
function MeM() {
  const groups = [
    ['账户', [['manage_accounts', '账户设置'], ['lock', '修改密码'], ['corporate_fare', '组织与角色']]],
    ['系统', [['smart_toy', 'AI / OCR 配置'], ['notifications', '消息通知'], ['monitoring', '系统监控']]],
  ];
  return (
    <div>
      <div style={{ background: 'linear-gradient(160deg,var(--teal-700),var(--teal-900))', padding: '24px 18px 30px', color: '#fff', display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'rgba(255,255,255,.18)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 22, fontWeight: 700 }}>管</div>
        <div><div style={{ fontSize: 18, fontWeight: 700 }}>管理员</div><div style={{ fontSize: 13, opacity: .8 }}>admin · 系统管理员</div></div>
      </div>
      <div className="m-pad" style={{ marginTop: -10 }}>
        {groups.map(([title, rows]) => (
          <div key={title} style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px 4px 8px', fontWeight: 600 }}>{title}</div>
            <div className="m-card" style={{ overflow: 'hidden' }}>
              {rows.map(([ic, label]) => (
                <div key={label} className="m-row"><span className="material-symbols-outlined" style={{ fontSize: 21, color: 'var(--text-secondary)' }}>{ic}</span><span style={{ flex: 1, fontSize: 15, color: 'var(--text-primary)' }}>{label}</span><span style={{ color: 'var(--text-tertiary)' }}>›</span></div>
              ))}
            </div>
          </div>
        ))}
        <button className="m-btn m-btn--sec" style={{ color: 'var(--danger)', borderColor: 'var(--danger-border)' }}>退出登录</button>
      </div>
    </div>
  );
}
Object.assign(window, { HomeM, CaptureM, StockM, MeM });
