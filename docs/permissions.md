# 权限矩阵 (角色与端点守卫)

系统有三种角色(`app/models/auth.py: ROLES`):

| 角色 | 说明 | 典型可做 |
|---|---|---|
| `admin` | 管理员 | 一切,含破坏性操作(删数据/回滚/清空/用户管理/改系统配置) |
| `operator` | 操作员 | 日常录入/导入/改业务数据,但**不能**做破坏性/治理操作 |
| `viewer` | 查看者 | 只读浏览,不能改任何数据 |

## 守卫约定(最小权限)

- **只读端点** (`GET`):允许 `viewer/operator/admin`(经 `require_role(...)` 或 `get_current_user`)。
- **写端点** (`POST/PUT/PATCH/DELETE` 业务数据):要求 `operator` 或 `admin`。
- **破坏性 / 治理端点**:**仅 `admin`**。已确认收紧到 admin 的:
  - 导入回滚 `POST /api/importer/jobs/{id}/rollback`(删数据)
  - 审计清理 `POST /api/audit/prune`、审计查看 `GET /api/audit/*`
  - 用户管理 `/api/auth/users*`、清空数据等管理操作 `/api/admin/*`

## 实现要点

- 守卫统一用 `app/dependencies.py: require_role(*roles)` 依赖;`get_current_user` 表示"登录即可"。
- 登录限流见 `app/rate_limit.py`(登录 10次/分钟·IP);限流 key 取 JWT 的 `uname`。
- 强制改默认密码:`must_change_password`(默认 admin/admin 必须首次改密)。

## 待办 / 复核建议

- [ ] 定期 grep `@router.(post|put|delete|patch)` 核对每个写端点都带 `require_role`/`get_current_user`,杜绝"裸端点"。
- [ ] 把 `viewer` 与 `operator` 的边界写进自动化测试(用 viewer token 访问写端点应 403)。
- [ ] 如需更细粒度(按模块授权),可在 `require_role` 之上加"按功能点"权限位,届时本表升级为 角色×功能 矩阵。

> 现状:绝大多数写端点已是 `require_role("admin","operator")`,破坏性端点已收紧到 `admin`。本文件作为权限基线,改动端点守卫时同步更新。
