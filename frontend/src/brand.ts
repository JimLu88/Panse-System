/** Build-time identity for each isolated ERP deployment. */
const code = String(import.meta.env.VITE_BRAND_CODE || 'PS').toUpperCase();

export const isFuge = code === 'FG';
export const brandCode = isFuge ? 'FG' : 'PS';
export const brandName = import.meta.env.VITE_BRAND_NAME || (isFuge ? '孚格' : '畔色');
export const erpTitle = `${brandName} ERP`;
export const shopName = import.meta.env.VITE_SHOP_NAME || (isFuge ? '孚格家具' : '畔色木作');

export function setDocumentBrand() {
  document.title = erpTitle;
}
