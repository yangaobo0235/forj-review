# 测试组织与业务边界

测试按实际需求维护。生产页面的权威配置是 PostgreSQL；测试不访问本机 `local` 或线上 `public`，也不向服务目录放业务默认数据。

## 目录

| 目录 | 验证内容 |
| --- | --- |
| `unit/businesses` | 青岛及长春报废置换、车源、过户的规则、路由与地区政策 |
| `unit/evidence`、`unit/presentation` | 证据可信度、比较、任务展示及回填限制 |
| `unit/capabilities`、`unit/runtime`、`unit/integrations` | 能力执行、异步任务生命周期、模型与二维码适配 |
| `integration` | 审核 HTTP、完整审核图及各业务的组合场景 |
| `configuration/unit` | 完整页面编译、普通字段增删改、开发属性保护、离线检查与回放 |
| `configuration/integration` | 字段全链路、现场采集、会话、助手协议、验证、发布与回滚 |
| `database` | 真实 PostgreSQL 的 ORM 查询、事务、不可变历史、并发、Schema 隔离及迁移 |
| `contracts` | 请求、证据和返回结构的接口约束 |
| `release`、`tooling` | 打包、部署回退、架构边界 |
| `fixtures` | 固定的脱敏页面声明和回放输入；不能作为生产初始化数据 |
| `support` | 测试工厂、模型替身和隔离数据库工具；测试文件之间不互相导入 |

## 实际需求对应的验证

| 需求 | 主要用例 |
| --- | --- |
| 已有页面只改配置即可增删普通字段 | `configuration/unit/test_field_maintenance.py`、`configuration/integration/test_field_changes.py` |
| 变更贯通采集、材料提取、比较与任务 | `configuration/integration/test_field_changes.py` |
| 手动维护和助手共用校验与保存规则 | `configuration/integration/test_daily_maintenance.py`、`test_publication.py` |
| 助手不能新增页面、新算法或绕过显式权限操作 | `configuration/unit/test_field_maintenance.py`、`configuration/integration/test_daily_maintenance.py` |
| 新版本发布、单页回退、整批回退、重复提交及并发冲突 | `configuration/integration/test_publication.py`、`database/test_relational_configuration.py`、`database/test_postgres_storage.py` |
| 车源车型筛选、无匹配、多候选、人工选择与回填 | `database/test_vehicle_model_catalog.py`、`integration/test_vehicle_source_workflow.py`、`unit/businesses/test_vehicle_source_configuration.py` |
| 不确定材料、缺失证据和过期现场不能生成可信回填 | `unit/evidence`、`integration/test_review_decision_safety.py`、`configuration/integration/test_collection_validation.py` |
| 缺少数据库配置时不能恢复内置页面 | `configuration/integration/test_field_changes.py`、`database/test_relational_configuration.py` |
| 历史摘要校验、增量迁移失败回滚及四张业务表保护 | `database/test_schema_v4_migration.py`、`database/test_migration_history.py` |
| 两后端错误分类、内部错误与请求数据不透出 | `contracts/test_http_errors.py` |

## 运行

```powershell
# 快速验证，不需要数据库
uv run pytest -q tests/unit tests/contracts tests/configuration/unit tests/tooling

# 审核、配置和数据库集成验证；默认自动创建一次性 PostgreSQL 容器
uv run pytest -q tests/integration tests/configuration/integration tests/database

# 完整验证，包含打包和部署工具
uv run pytest -q
```

可通过 `FORJ_TEST_POSTGRES_URL` 复用本机专用测试实例。仅允许 loopback 地址；每个用例创建唯一的 `forj_test_*` Schema 并在结束时删除。不要填写 IDEA 转发到真实业务库的连接。测试会屏蔽工作区私密数据库配置，日志写临时目录。

四个业务页面夹具保存在 `fixtures/pages`，使用独立完整 2.0 声明，业务版本固定为 `1.0`。它们是可审查的固定输入，修改生产数据库不会改变测试预期。`fixtures/permit.json` 用于配置功能的合成页面；`fixtures/replay` 保存人工确定的结果。生产程序不会读取这些目录。

## 增删测试的规则

保留可解释的业务结果、权限边界、故障处理和事务保障。新增测试应说明触发条件与预期行为；不要只比较两个从同一输入生成的对象，或把实现细节抄成断言。

旧版文件历史、增量模板继承、内置声明转换与旧快照跨 Schema 初始化测试已经删除。数据库显式迁移仍有保护源数据的测试，因为未来首次升级仍需要这个工具。接口和持久化会话仍实际使用的兼容行为继续验证，不按文件名中的“legacy”一概删除。

前端测试分别放在两个应用的 `tests` 中；浏览器真实采集和回填用例位于扩展的 `tests/browser`。从根目录运行 `npm run test:extension`、`npm run test:configuration`、`npm run test:extension:browser`。


版本 4 增加 `database/test_configuration_evidence.py`（完成证据、旧修订覆盖拦截、禁止追加正式子项），`unit/runtime/test_configuration_reload.py`（完整编译后切换、失败保留旧服务）。`unit/runtime/test_jobs.py` 直接验证在途审核作业固定旧服务；日常维护集成测试分别实际执行无模型工作台发布与 CLI 发布，并核对重复提交。
