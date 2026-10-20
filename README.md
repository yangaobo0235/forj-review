# FORJ 审核辅助系统

项目名称：`forj-review`。帮助审核员识别车辆材料、核对证据和执行审核规则，并通过审核配置工作台维护各业务页面的审核方式。审核建议供人工处理；系统不代替审核员提交审批。

## 两个功能，四个模块

| 功能 | 模块 | 代码目录 | 运行位置 |
| --- | --- | --- | --- |
| 审核辅助 | 审核浏览器扩展 | `apps/review-extension` | 使用者浏览器；界面开发预览 26199 |
| 审核辅助 | 审核服务 | `src/forj_review_service` | 服务器；开发时可在本机 26101 运行 |
| 审核配置 | 审核配置工作台 | `apps/review-config-web` | 本机 26198 |
| 审核配置 | 审核配置服务 | `src/forj_review_config_service` | 本机 26102 |

当前配置包括青岛、长春报废置换、车源和过户审核。一致性审核保留未配置的人工复核入口。配置功能仍在本机开发和验收，不包含在服务器上线范围中。

## 文档入口

| 想了解什么 | 阅读文档 |
| --- | --- |
| 每个目录做什么、修改功能应去哪里 | [项目结构](docs/项目结构.md) |
| 安装依赖、配置环境、IDEA 启动与检查 | [本地开发](docs/本地开发.md) |
| 审核员怎样使用扩展 | [审核辅助使用](docs/审核辅助使用.md) |
| 如何通过工作台维护审核配置 | [审核配置使用](docs/审核配置使用.md) |
| 识别、证据、规则、任务与页面动作怎样协作 | [审核执行原理](docs/审核执行原理.md) |
| 配置格式、离线回放、新增业务与能力 | [审核配置开发](docs/审核配置开发.md) |
| 打包、上传、PostgreSQL、日志、更新与回滚 | [部署运维](docs/部署运维.md) |
| 数据表、字段注释、关联、约束与维护约定 | [数据库设计](docs/数据库设计.md) |

具体业务规则：[青岛报废置换](docs/业务/报废置换-青岛.md)、[长春报废置换](docs/业务/报废置换-长春.md)、[车源审核](docs/业务/车源审核.md)、[过户审核](docs/业务/过户审核.md)。新增规则使用[业务文档模板](docs/业务/业务文档模板.md)。协作规范见 [CONTRIBUTING](.github/CONTRIBUTING.md)，安全反馈见 [SECURITY](.github/SECURITY.md)。

## 本地快速入口

需要 Python 3.11/3.12、uv、Node.js 22.18+。先按[本地开发](docs/本地开发.md)准备已有 PostgreSQL 连接和各服务的模型配置，再在仓库根目录执行：

```powershell
uv sync --locked
npm --prefix apps/review-extension ci
npm --prefix apps/review-config-web ci
```

按需在不同终端启动：

```powershell
uv run forj-review-service --reload
uv run forj-review-config-service
npm run dev:configuration
```

扩展执行 `npm run build:extension`，在浏览器加载 `apps/review-extension/dist`，随后刷新业务页面。

两个后端仅使用 PostgreSQL，共享私密 `.env.database`，分别读取各自包下的模型 `.env`。本机与服务器统一连接 `forj_review.public`，共享页面配置和车型目录。审核任务与结果暂存内存并过期清理；配置会话、页面快照及车型目录保存在 PostgreSQL。服务启动不会建库或迁移。

数据库实例供多个项目共用，各项目独立建库、共用 `postgres` 账号。本项目连接使用标准 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`、`PGSSLMODE`；实例安装与连接配置见[部署运维](docs/部署运维.md)。

## 打包入口

```powershell
.\scripts\release\package-server.cmd
.\scripts\release\package-extension.cmd
```

`package-server.cmd` 只生成 `release/server/forj-review-server.tar.gz`，不导出脚本或配置；扩展安装包输出到 `release/extension/`。

**首次部署固定文件直接保存在 `deploy/server/`，无需生成。** 将该目录内的固定文件和后端压缩包一起上传到 `/home/ubuntu/forj-review/`，执行 `bash setup-server.sh`、填写密码后运行 `./start-server.sh`。**日常更新只打包、上传后端压缩包**，继续使用服务器原有脚本执行停止和启动。详细步骤见[部署运维](docs/部署运维.md)。
