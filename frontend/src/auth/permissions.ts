/**
 * 子账号页面权限 (页面级 RBAC) — 前端权威定义。
 *
 * - 每个「可分配页面」有一个 permKey。主账号(admin)/未设限账号(page_perms==null) 看全部;
 *   受限子账号只看 page_perms 里列出的页面, 其余菜单不显示、直接输网址/自动跳转也会渲染「程序错误」。
 * - permKey 必须与 App.tsx 菜单项 key、后端 app/page_permissions.py 的 PERM_KEYS 三方一致。
 * - role(admin/operator/viewer) 与 page_perms 正交: role 管能否写, page_perms 管能看到哪些页面。
 */
import type { MeUser } from '../api/auth';

/** 后端专属哨兵: 只有 admin 能过 (受限子账号永远拿不到这个 key)。 */
export const ADMIN_ONLY = '__admin_only__';

export interface PermGroup {
  key: string;
  label: string;
  children: { key: string; label: string }[];
}

/** 可分配权限树 — 管理端「子账号可见页面」勾选框用; 顺序/分组与顶栏菜单一致。 */
export const PERM_TREE: PermGroup[] = [
  { key: 'g-data', label: '数据分析', children: [
    { key: 'dashboard', label: '数据大盘' },
    { key: 'reports', label: '报表' },
  ] },
  { key: 'g-product', label: '产品', children: [
    { key: 'products', label: '产品总表' },
    { key: 'bom-list', label: 'BOM 清单 (含尺寸复核)' },
    { key: 'materials', label: '物料单价库' },
    { key: 'new-product', label: '新产品录入' },
    { key: 'npd', label: '新品开发' },
    { key: 'taobao-listings', label: '淘宝对应表' },
  ] },
  { key: 'g-price', label: '价格', children: [
    { key: 'pricing', label: '定价表' },
    { key: 'customization', label: '竞品价库' },
    { key: 'custom-quote-v2', label: '定制报价 (顶栏按钮)' },
  ] },
  { key: 'g-stock', label: '库存', children: [
    { key: 'inventory', label: '配件库存' },
    { key: 'product-inventory', label: '成品库存' },
    { key: 'samples', label: '样品库存' },
    { key: 'marketing-wood', label: '木材损耗' },
  ] },
  { key: 'g-order', label: '订单', children: [
    { key: 'orders', label: '订单' },
    { key: 'orders-kanban', label: '看板（含配件备料）' },
    { key: 'custom-reconcile', label: '定制单核对' },
    { key: 'customers', label: '客户' },
    { key: 'aftersales', label: '退货/售后' },
  ] },
  { key: 'g-logistics', label: '物流', children: [
    { key: 'logistics-bills', label: '物流账单' },
    { key: 'packing-bills', label: '打包费账单' },
    { key: 'wanshifu-bills', label: '万师傅' },
  ] },
  { key: 'g-marketing', label: '营销', children: [
    { key: 'marketing-promotion', label: '推广记录' },
    { key: 'promotion-flows', label: '推广费流水' },
    { key: 'marketing-brand', label: '品牌营销' },
    { key: 'refill-records', label: '补单记录' },
    { key: 'marketing-daily', label: '日常经营' },
    { key: 'review-assets', label: '评价资产' },
  ] },
  { key: 'g-supply', label: '供应链', children: [
    { key: 'suppliers', label: '供应商 (含评分)' },
    { key: 'purchases', label: '配件采购' },
    { key: 'monthly-settlement', label: '月结对账中心' },
    { key: 'factory-orders', label: '工厂下单表' },
    { key: 'factory-statement', label: '工厂对账单' },
    { key: 'factory-settlement', label: '工厂月结销账' },
  ] },
  { key: 'g-finance', label: '财务', children: [
    { key: 'assets-cashflow', label: '资产 & 流水' },
    { key: 'alipay', label: '支付宝流水' },
    { key: 'account-balances', label: '账户余额' },
    { key: 'staff-salary', label: '人员工资' },
    { key: 'per-order-reconcile', label: '逐单核对' },
    { key: 'recon-center', label: '对账中心' },
  ] },
  { key: 'g-todo', label: '待办', children: [
    { key: 'ops-checklist', label: '待办事项' },
  ] },
  { key: 'g-tools', label: '工具', children: [
    { key: 'web-agent', label: '自动取数' },
    { key: 'importer', label: 'Excel 导入' },
    { key: 'data-export', label: 'Excel 导出' },
    { key: 'import-archive', label: '资料存档库' },
    { key: 'audit-trail', label: '修改历史' },
    { key: 'feishu', label: '飞书' },
  ] },
];

/** 所有可分配 permKey (校验/全选用)。 */
export const ALL_PERM_KEYS: string[] = PERM_TREE.flatMap((g) => g.children.map((c) => c.key));

// 路径 → permKey 精确表 (含已删页面的重定向源, 避免跳转瞬间闪「程序错误」)。
const EXACT_PATH_PERM: Record<string, string> = {
  '/dashboard': 'dashboard', '/sales-ranking': 'dashboard', '/forecast': 'dashboard', '/exceptions': 'dashboard',
  '/reports': 'reports',
  '/products': 'products',
  '/bom-list': 'bom-list', '/bom-size-review': 'bom-list',
  '/materials': 'materials',
  '/new-product': 'new-product',
  '/npd': 'npd',
  '/taobao-listings': 'taobao-listings',
  '/pricing': 'pricing', '/pricing-formulas': 'pricing', '/quote': 'pricing',
  '/shop-price-board': 'pricing',

  '/customization': 'customization',
  '/custom-quote-v2': 'custom-quote-v2',
  '/inventory': 'inventory',
  '/product-inventory': 'product-inventory', '/producibility': 'product-inventory',
  '/samples': 'samples',
  '/orders': 'orders', '/orders/kanban': 'orders-kanban', '/orders/custom-reconcile': 'custom-reconcile',
  '/customers': 'customers',
  '/aftersales': 'aftersales',
  '/logistics-bills': 'logistics-bills', '/packing-bills': 'packing-bills', '/wanshifu-bills': 'wanshifu-bills',
  '/promotion-flows': 'promotion-flows', '/refill-records': 'refill-records',
  '/suppliers': 'suppliers', '/purchases': 'purchases',
  '/monthly-settlement': 'monthly-settlement',
  '/factory-orders': 'factory-orders', '/factory-statement': 'factory-statement', '/factory-settlement': 'factory-settlement',
  '/assets-cashflow': 'assets-cashflow', '/assets': 'assets-cashflow', '/cash-flow': 'assets-cashflow', '/shop-deposits': 'assets-cashflow',
  '/alipay': 'alipay',
  '/account-balances': 'account-balances', '/accounting': 'account-balances',
  '/staff-salary': 'staff-salary',
  '/per-order-reconcile': 'per-order-reconcile',
  '/recon-center': 'recon-center', '/reconciliation': 'recon-center', '/settlements': 'recon-center',
  '/recon-diagnostics': 'recon-center', '/factory-recon': 'recon-center', '/prepay-ledger': 'recon-center',
  '/ops-checklist': 'ops-checklist',
  '/web-agent': 'web-agent', '/importer': 'importer', '/data-export': 'data-export',
  '/import-archive': 'import-archive', '/audit-trail': 'audit-trail', '/feishu': 'feishu',
  // 管理员专属 (受限子账号一律「程序错误」; admin 短路放行)
  '/admin': ADMIN_ONLY, '/ops-tools': ADMIN_ONLY, '/data-explorer': ADMIN_ONLY,
};

/** 当前 location → 所需 permKey; null = 不设限页面(公共/未知), 放行。 */
export function resolvePagePerm(pathname: string, search: string): string | null {
  const p = (pathname || '/').replace(/\/+$/, '') || '/';
  // 营销页多子项按 ?tab= 区分 (人员外包已删 → 归 promotion)
  if (p === '/marketing') {
    const tab = new URLSearchParams(search).get('tab');
    if (tab === 'brand') return 'marketing-brand';
    if (tab === 'daily') return 'marketing-daily';
    if (tab === 'wood_loss') return 'marketing-wood';
    return 'marketing-promotion';
  }
  const exact = EXACT_PATH_PERM[p];
  if (exact !== undefined) return exact;
  // 动态/子路由归到父页面 permKey
  if (p.startsWith('/bom/')) return 'bom-list';
  if (p.startsWith('/npd/')) return 'npd';
  if (p.startsWith('/orders/')) return 'orders';   // 如 /orders/:id/factory-sheet
  return null;   // 未登记 (如 /ai / 首页重定向) → 放行
}

/** 主账号 / 未设限账号: 看全部。 */
export function isUnrestricted(user: MeUser | null | undefined): boolean {
  return !user || user.role === 'admin' || user.page_perms == null;
}

/** 该用户能否访问某 permKey。 */
export function canAccessPerm(user: MeUser | null | undefined, permKey: string | null): boolean {
  if (permKey === null) return true;          // 公共/未登记页
  if (isUnrestricted(user)) return true;      // admin / 主账号 / 存量账号
  if (permKey === ADMIN_ONLY) return false;   // 管理员专属, 受限子账号拿不到
  return (user!.page_perms || []).includes(permKey);
}

// permKey → 落地路由 (多数 = /{key}; 少数带 ?tab= 或子路由的在此列出)
const PERM_KEY_TO_PATH: Record<string, string> = {
  'marketing-promotion': '/marketing?tab=promotion',
  'marketing-brand': '/marketing?tab=brand',
  'marketing-daily': '/marketing?tab=daily',
  'marketing-wood': '/marketing?tab=wood_loss',
  'customization': '/customization?tab=competitor',
  'orders-kanban': '/orders/kanban',
  'custom-reconcile': '/orders/custom-reconcile',
  'custom-quote-v2': '/custom-quote-v2',
};
function pathForPermKey(key: string): string {
  return PERM_KEY_TO_PATH[key] ?? `/${key}`;
}

/** 登录后的落地页: admin/主账号→数据大盘; 受限子账号→其第一个有权的页面(按菜单顺序, 通常是产品总表)。 */
export function homePathFor(user: MeUser | null | undefined): string {
  if (isUnrestricted(user)) return '/dashboard';
  const allowed = new Set(user!.page_perms || []);
  for (const g of PERM_TREE) {
    for (const c of g.children) {
      if (allowed.has(c.key)) return pathForPermKey(c.key);
    }
  }
  return '/dashboard';   // 兜底 (受限账号理论上至少开通一页)
}

interface MenuNode { key: string; label?: unknown; children?: MenuNode[] }

/** 按权限过滤 antd 菜单项: 删掉无权叶子, 再删掉空了的分组。 */
export function filterMenuByPerms<T extends MenuNode>(items: T[], user: MeUser | null | undefined): T[] {
  if (isUnrestricted(user)) return items;     // 全看, 原样返回 (不改引用外结构)
  const out: T[] = [];
  for (const it of items) {
    if (it.children && it.children.length) {
      const kids = it.children.filter((c) => canAccessPerm(user, c.key));
      if (kids.length) out.push({ ...it, children: kids });
    } else if (canAccessPerm(user, it.key)) {
      out.push(it);
    }
  }
  return out;
}
