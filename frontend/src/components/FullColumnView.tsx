import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Dropdown,
  Empty,
  Input,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  ColumnHeightOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/base';

const { Text } = Typography;

interface ColumnMeta {
  key: string;
  label: string;
  type: string;
  is_core: boolean;
}

interface TableData {
  entity: string;
  label: string;
  columns: ColumnMeta[];
  total: number;
  rows: Record<string, any>[];
}

const PAGE_SIZE = 50;
const LS_PREFIX = 'panse_fullcol_core_';

const fetchTableData = (entity: string, q: string, page: number) =>
  api
    .get<TableData>(`/api/table-explorer/${entity}`, {
      params: { q: q || undefined, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE },
    })
    .then((r: { data: TableData }) => r.data);

function renderCell(value: any, type: string) {
  if (value === null || value === undefined || value === '') return <Text type="secondary">-</Text>;
  if (type === 'bool') return value ? <Tag color="green">是</Tag> : <Tag>否</Tag>;
  if (type === 'decimal' && typeof value === 'number') {
    return <span>{value.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>;
  }
  if ((type === 'datetime' || type === 'date') && typeof value === 'string') {
    return <span>{value.replace('T', ' ').slice(0, type === 'date' ? 10 : 19)}</span>;
  }
  const text = String(value);
  // 长内容(尺寸明细/定制范围等)列窄会被省略号截断 → 悬停显示全文, 方便看到后面
  if (text.length > 14) {
    return <Tooltip title={text} placement="topLeft" overlayStyle={{ maxWidth: 520 }}><span>{text}</span></Tooltip>;
  }
  return <span>{text}</span>;
}

/**
 * 可复用「全列视图」：任意业务表都能显示全部列，
 * 支持核心列默认显示 + 一键展开全部列 + 自定义核心列(localStorage)。
 *
 * 用 entity (table-explorer 的实体名) 驱动，自带数据获取。
 */
export default function FullColumnView({
  entity,
  defaultShowAll = false,
  searchPlaceholder = '搜索...',
}: {
  entity: string;
  defaultShowAll?: boolean;
  searchPlaceholder?: string;
}) {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(defaultShowAll);
  const [customCore, setCustomCore] = useState<string[] | null>(null);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['fullcol', entity, search, page],
    queryFn: () => fetchTableData(entity, search, page),
  });

  useEffect(() => {
    const saved = localStorage.getItem(LS_PREFIX + entity);
    if (saved) {
      try {
        setCustomCore(JSON.parse(saved));
      } catch {
        setCustomCore(null);
      }
    } else {
      setCustomCore(null);
    }
    setPage(1);
  }, [entity]);

  const allColumns = data?.columns || [];

  const effectiveCore = useMemo(() => {
    if (customCore && customCore.length) return new Set(customCore);
    return new Set(allColumns.filter((c) => c.is_core).map((c) => c.key));
  }, [customCore, allColumns]);

  const visibleColumns = useMemo(() => {
    const cols = showAll ? allColumns : allColumns.filter((c) => effectiveCore.has(c.key));
    return cols.map((c) => {
      // 统一收窄: 长文案(产品文案/尺寸明细)不再撑爆整行, 截断后悬停看全文
      const w = c.type === 'datetime' ? 150 : c.type === 'str' ? 150 : 110;
      return {
        title: (
          <Tooltip title={c.key}>
            <span>{c.label}</span>
          </Tooltip>
        ),
        dataIndex: c.key,
        key: c.key,
        width: w,
        ellipsis: true,
        onCell: () => ({
          style: { maxWidth: w, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const },
        }),
        render: (v: any) => renderCell(v, c.type),
      };
    });
  }, [showAll, allColumns, effectiveCore]);

  const saveCustomCore = (keys: string[]) => {
    setCustomCore(keys);
    localStorage.setItem(LS_PREFIX + entity, JSON.stringify(keys));
  };

  const coreConfigMenu = (
    <Card size="small" style={{ width: 320, maxHeight: 420, overflow: 'auto' }}>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Text strong>选择核心列（折叠时默认显示）</Text>
        <Checkbox.Group
          value={Array.from(effectiveCore)}
          onChange={(vals) => saveCustomCore(vals as string[])}
          style={{ width: '100%' }}
        >
          <Space direction="vertical" style={{ width: '100%' }}>
            {allColumns.map((c) => (
              <Checkbox key={c.key} value={c.key}>
                {c.label}{' '}
                <Text type="secondary" style={{ fontSize: 11 }}>
                  ({c.key})
                </Text>
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
        <Button
          size="small"
          block
          onClick={() => {
            localStorage.removeItem(LS_PREFIX + entity);
            setCustomCore(null);
          }}
        >
          恢复默认核心列
        </Button>
      </Space>
    </Card>
  );

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="small">
      <Space wrap>
        <Input.Search
          placeholder={searchPlaceholder}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onSearch={(val) => {
            setSearch(val);
            setPage(1);
          }}
          style={{ width: 240 }}
          allowClear
        />
        <Tooltip title={showAll ? '当前显示全部列' : '当前只显示核心列'}>
          <Button
            type={showAll ? 'primary' : 'default'}
            icon={<ColumnHeightOutlined />}
            onClick={() => setShowAll((s) => !s)}
          >
            {showAll ? '收起为核心列' : '展开全部列'}
          </Button>
        </Tooltip>
        <Dropdown dropdownRender={() => coreConfigMenu} trigger={['click']} placement="bottomRight">
          <Button icon={<SettingOutlined />}>自定义核心列</Button>
        </Dropdown>
        <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
          刷新
        </Button>
        <Badge
          status="processing"
          text={
            <Text type="secondary">
              {data ? (
                <>
                  共 <strong>{data.total}</strong> 行 · 显示{' '}
                  <strong>{visibleColumns.length}</strong>/{allColumns.length} 列
                  {showAll ? '（全部列）' : '（核心列）'}
                </>
              ) : (
                '加载中...'
              )}
            </Text>
          }
        />
      </Space>

      {allColumns.length === 0 && !isLoading ? (
        <Empty description="该表暂无数据或列" />
      ) : (
        <Table
          dataSource={data?.rows || []}
          columns={visibleColumns}
          rowKey={(r) => r.id ?? JSON.stringify(r).slice(0, 50)}
          loading={isLoading}
          size="small"
          scroll={{ x: 'max-content' }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total || 0,
            showSizeChanger: false,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 行`,
          }}
        />
      )}
    </Space>
  );
}
