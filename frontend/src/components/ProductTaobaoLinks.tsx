import { Space, Typography } from 'antd';
import type { TaobaoLinkInfo } from '../api/catalog';

export default function ProductTaobaoLinks({ row }: { row: {
  taobao_links?: TaobaoLinkInfo[]; taobao_link_status?: string;
} }) {
  const links = row.taobao_links ?? [];
  return <Space direction="vertical" size={0}>
    {links.map((link) => <a key={link.url} href={link.url} target="_blank"
      rel="noopener noreferrer" onClick={(event) => event.stopPropagation()}
      title={`商品 ${link.item_id}${link.sku_id ? ` / SKU ${link.sku_id}` : ''}`}>
      {link.label}{links.length > 1 ? ` ${link.item_id}` : ''} ↗
    </a>)}
    {(!links.length || row.taobao_link_status !== '款式已映射') &&
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {row.taobao_link_status || '缺淘宝商品映射'}
      </Typography.Text>}
  </Space>;
}
