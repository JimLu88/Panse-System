// 登录页 — 居中卡片，呼应「畔色」水岸主色渐变
function LoginScreen({ onLogin }) {
  const [u, setU] = React.useState('admin');
  const [p, setP] = React.useState('admin');
  return (
    <div style={{ minHeight: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'radial-gradient(1200px 600px at 70% -10%, var(--teal-50), transparent), var(--bg-app)', padding: 24 }}>
      <div className="k-card" style={{ width: 380, boxShadow: 'var(--shadow-lg)', borderRadius: 'var(--radius-2xl)' }}>
        <div style={{ padding: '34px 32px 28px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11, justifyContent: 'center', marginBottom: 6 }}>
            <span className="k-nav__logo" style={{ width: 38, height: 38, borderRadius: 11, fontSize: 19 }}>畔</span>
            <div style={{ fontSize: 22, fontWeight: 800, letterSpacing: '-.02em', color: 'var(--text-primary)' }}>畔色孚格 ERP</div>
          </div>
          <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13, marginBottom: 26 }}>家具电商内部管理系统</div>
          <div className="k-field" style={{ marginBottom: 14 }}>
            <span className="k-field__label">用户名</span>
            <div className="k-input"><span className="affix">👤</span><input value={u} onChange={(e) => setU(e.target.value)} placeholder="用户名" /></div>
          </div>
          <div className="k-field" style={{ marginBottom: 22 }}>
            <span className="k-field__label">密码</span>
            <div className="k-input"><span className="affix">🔒</span><input type="password" value={p} onChange={(e) => setP(e.target.value)} placeholder="密码" /></div>
          </div>
          <KBtn variant="primary" size="lg" block onClick={onLogin}>登录</KBtn>
          <div style={{ marginTop: 16, fontSize: 12, color: 'var(--text-tertiary)', textAlign: 'center', lineHeight: 1.6 }}>
            默认管理员 <code style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>admin / admin</code><br />登录后请立即修改密码
          </div>
        </div>
      </div>
    </div>
  );
}
window.LoginScreen = LoginScreen;
