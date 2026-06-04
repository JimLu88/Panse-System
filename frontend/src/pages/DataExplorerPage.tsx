import { useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Dropdown,
  Empty,
  Input,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import {
  AppstoreOutlined,
  ColumnHeightOutlined,
  ReloadOutlined,
  SettingOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/base';

const { Text, Title } = Typography;

interface EntityMeta {
  value: string;
  label: string;
  row_count: number;
}

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
const LS_PREFIX = 'panse_explorer_core_';

const fetchEntities = () =>
  api.get<EntityMeta[]>('/api/table-explorer/entities').then((r: { data: EntityMeta[] }) => r.data);

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
  return <span>{String(value)}</span>;
}

export default function DataExplorerPage() {
  const [entity, setEntity] = useState<string>('order');
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [showAll, setShowAll] = useState(false);
  // 用户自定义核心列 (按表持久化到 localStorage)
  const [customCore, setCustomCore] = useState<Record<string, string[]>>({});

  const { data: entities = [] } = useQuery({
    queryKey: ['explorer-entities'],
    queryFn: fetchEntities,
  });

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['explorer-data', entity, search, page],
    queryFn: () => fetchTableData(entity, search, page),
    enabled: !!entity,
  });

  // 切换表时加载该表保存的核心列设置
  useEffect(() => {
    const saved = localStorage.getItem(LS_PREFIX + entity);
    if (saved) {
      try {
        setCustomCore((prev) => ({ ...prev, [entity]: JSON.parse(saved) }));
      } catch {
        /* ignore */
      }
    }
    setPage(1);
    setShowAll(false);
  }, [entity]);

  const allColumns = data?.columns || [];

  // 当前生效的核心列：用户自定义 > 后端默认 is_core
  const effectiveCore = useMemo(() => {
    const userSel = customCore[entity];
    if (userSel && userSel.length) return new Set(userSel);
    return new Set(allColumns.filter((c) => c.is_core).map((c) => c.key));
  }, [customCore, entity, allColumns]);

  const visibleColumns = useMemo(() => {
    const cols = showAll ? allColumns : allColumns.filter((c) => effectiveCore.has(c.key));
    return cols.map((c) => ({
      title: (
        <Tooltip title={c.key}>
          <span>{c.label}</span>
        </Tooltip>
      ),
      dataIndex: c.key,
      key: c.key,
      width: c.type === 'str' ? 160 : 120,
      ellipsis: true,
      render: (v: any) => renderCell(v, c.type),
    }));
  }, [showAll, allColumns, effectiveCore]);

  const saveCustomCore = (keys: string[]) => {
    setCustomCore((prev) => ({ ...prev, [entity]: keys }));
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
                {c.label} <Text type="secondary" style={{ fontSize: 11 }}>({c.key})</Text>
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
        <Button
          size="small"
          block
          onClick={() => {
            localStorage.removeItem(LS_PREFIX + entity);
            setCustomCore((prev) => {
              const next = { ...prev };
              delete next[entity];
              return next;
            });
          }}
        >
          恢复默认核心列
        </Button>
      </Space>
    </Card>
  );

  return (
    <div>
      <Row align="middle" justify="space-between" style={{ marginBottom: 16 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>
            <TableOutlined style={{ marginRight: 8 }} />
            全列数据浏览
          </Title>
          <Text type="secondary">
            所有业务表的完整列都可在此查看。默认显示核心列，点「展开全部列」看全部。
          </Text>
        </Col>
      </Row>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <span>
            <Text type="secondary">选择表：</Text>
            <Select
              value={entity}
              onChange={setEntity}
              style={{ width: 220 }}
              showSearch
              optionFilterProp="label"
              options={entities.map((e) => ({
                value: e.value,
                label: `${e.label} (${e.row_count})`,
              }))}
            />
          </span>
          <Input.Search
            placeholder="搜索..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onSearch={(val) => {
              setSearch(val);
              setPage(1);
            }}
            style={{ width: 220 }}
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
          <Dropdown
            dropdownRender={() => coreConfigMenu}
            trigger={['click']}
            placement="bottomRight"
          >
            <Button icon={<SettingOutlined />}>自定义核心列</Button>
          </Dropdown>
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
            刷新
          </Button>
        </Space>
      </Card>

      <Card size="small">
        <Space style={{ marginBottom: 8 }}>
          <Badge
            status="processing"
            text={
              <Text type="secondary">
                {data ? (
                  <>
                    共 <strong>{data.total}</strong> 行 ·{' '}
                    显示 <strong>{visibleColumns.length}</strong>/{allColumns.length} 列
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
      </Card>
    </div>
  );
}
