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

已有页面的普通字段可以通过手动表单或助手维护，通过版本化数据库配置生效。新页面与特殊能力由开发接入；完整配置、历史版本和回执统一存入 PostgreSQL。现有数据的显式迁移及 CLI 维护见[审核配置开发](docs/开发/审核配置开发.md)。

## 文档入口

完整导航见 [docs/README.md](docs/README.md)。

| 想了解什么 | 阅读文档 |
| --- | --- |
| 开发流程、编码与验证、AI 如何维护项目 | [开发规范](docs/开发/开发规范.md) |
| 每个目录做什么、修改功能应去哪里 | [项目结构](docs/开发/项目结构.md) |
| 安装依赖、配置环境、IDEA 启动与检查 | [本地开发](docs/开发/本地开发.md) |
| 审核员怎样使用扩展 | [审核辅助使用](docs/使用/审核辅助使用.md) |
| 如何通过工作台维护审核配置 | [审核配置使用](docs/使用/审核配置使用.md) |
| 识别、证据、规则、任务与页面动作怎样协作 | [审核执行原理](docs/开发/审核执行原理.md) |
| 配置格式、离线回放、新增业务与能力 | [审核配置开发](docs/开发/审核配置开发.md) |
| 打包、上传、PostgreSQL、日志、更新与回滚 | [部署运维](docs/运维/部署运维.md) |
| 数据表、字段注释、关联、约束与维护约定 | [数据库设计](docs/数据/数据库设计.md) |
| 全部表字段、类型、默认值、约束与索引 | [数据库字段字典](docs/数据/数据库字段字典.md) |

具体业务规则：[青岛报废置换](docs/业务/报废置换-青岛.md)、[长春报废置换](docs/业务/报废置换-长春.md)、[车源审核](docs/业务/车源审核.md)、[过户审核](docs/业务/过户审核.md)。新增规则使用[业务文档模板](docs/模板/审核业务文档模板.md)。协作规范见 [CONTRIBUTING](.github/CONTRIBUTING.md)，安全反馈见 [SECURITY](.github/SECURITY.md)。

文档分类与职责按导航固定，优先更新已有文档。临时开发计划入口为 `docs/开发计划/README.md`，整个计划目录不纳入 Git；AI 开发入口见 [AGENTS.md](AGENTS.md)。

## 本地快速入口

需要 Python 3.11/3.12、uv、Node.js 22.18+。先按[本地开发](docs/开发/本地开发.md)准备已有 PostgreSQL 连接和各服务的模型配置，再在仓库根目录执行：

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

两个后端仅使用 PostgreSQL，共享私密 `.env.database`，分别读取各自包下的模型 `.env`。本机连接 `forj_review.local`，线上继续连接 `forj_review.public`；两者各有业务数据和配置，互不自动同步。审核任务与结果暂存内存并过期清理；配置会话、页面版本、字段、材料、发布历史及车型目录保存在 PostgreSQL。服务启动不会建库或迁移。

数据库实例供多个项目共用，各项目独立建库、共用 `postgres` 账号。本项目连接使用标准 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`、`PGSSLMODE`；实例安装与连接配置见[部署运维](docs/运维/部署运维.md)。

## 打包入口

```powershell
.\scripts\release\package-server.cmd
.\scripts\release\package-extension.cmd
```

`package-server.cmd` 只生成 `release/server/forj-review-server.tar.gz`，不导出脚本或配置；扩展安装包输出到 `release/extension/`。

**首次部署固定文件直接保存在 `deploy/server/`，无需生成。** 将该目录内的固定文件和后端压缩包一起上传到 `/home/ubuntu/forj-review/`，执行 `bash setup-server.sh`、填写密码后运行 `./start-server.sh`。首次发布关系存储版本时，需要更新部署脚本和 Compose，并显式迁移目标 Schema；启动脚本会在替换容器前预检。之后日常更新上传后端压缩包并启动，无需提前停止旧容器。详细步骤见[部署运维](docs/运维/部署运维.md)。
