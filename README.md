# forj-review-assistant

FORJ Review Assistant 是一个面向车辆审核页面的浏览器扩展与审核服务。系统通过统一的 LangGraph（流程图编排框架）主图处理材料采集、文档识别、证据聚合、字段比较、业务规则和审核任务生成；前端只负责页面采集、任务呈现和受控页面操作。

当前已配置的审核业务包括青岛和长春的报废置换审核、车源审核和过户审核。一致性审核保留为未配置（`rules_configured=false`）的人工复核入口。

普通页面和字段调整优先使用[声明式页面配置](docs/指南/声明式页面配置.md)：一份 JSON 可新增页面或继承现有版本，字段的提取白名单、材料映射和采集清单由后端生成，前端不增加业务规则。启用前执行 `uv run forj-review-assistant --check-pages review-pages`，再设置 `REVIEW_PAGES_DIR` 并重启服务。独立的[本地聊天配置 Agent](docs/指南/聊天审核配置Agent.md)已接入，用于通过聊天编辑、验证和确认保存配置；真实业务页完整验收仍需完成。

当前先在本地使用；有效配置预览、版本差异、离线回放和历史快照见[本地页面配置工作流](docs/指南/本地页面配置工作流.md)。这些命令不启动服务、不调用模型，也不发布到服务器。

## 文档入口

- [文档总索引](docs/索引.md)
- [项目结构与迁移](docs/指南/项目结构与迁移.md)：目录树、职责、命名、旧路径对照与升级步骤
- [业务底座与遗留审计](docs/参考/业务底座改造与遗留审计.md)：结构判断、已处理耦合及保留的兼容边界

## 安装与启动

本地采用两个前端、两个后端：浏览器扩展负责审核界面和页面采集；独立 Web 负责配置聊天。`.\start-review-local.cmd` 启动审核服务（8010），`.\start-configuration-local.cmd` 启动配置服务（8011），`.\start-configuration-web.cmd` 启动独立 Web（5174）。按需运行，生命周期独立。本机无需凭证或配对，详见[使用指南](docs/指南/聊天审核配置Agent.md)。

在仓库根目录执行，需要 Python 3.11/3.12、uv、Node.js 22.18 或更新版本：

```powershell
uv sync --locked
npm --prefix apps/extension ci
npm --prefix apps/configuration-web ci
# 首次使用：在两个后端各自的 .env 填写模型密钥
Copy-Item src/forj_review_assistant/.env.example src/forj_review_assistant/.env
Copy-Item src/forj_configuration_agent/.env.example src/forj_configuration_agent/.env
uv run forj-review-assistant --reload
```

两个后端只读取自己包下的 `.env`，不自动读取根目录 `.env`。模型凭据分别使用 `DASHSCOPE_API_KEY` 和 `CONFIGURATION_API_KEY`；两份模板的 `REVIEW_PAGES_DIR=../../review-pages` 解析到同一个正式配置目录。本文件内的目录值相对于 `.env` 所在目录解析，进程环境变量仍优先。已有根目录 `.env` 保留但不再加载，请自行按服务迁移需要的值，勿用模板覆盖已有配置。

前端的可选示例位于 `apps/extension/.env.example` 和 `apps/configuration-web/.env.example`，仅包含本地 API 地址，无需模型密钥。IDEA 两个后端的模块、端口和工作目录不变；移除以前手填的模型凭据或 `REVIEW_PAGES_DIR` 环境变量，让各自 `.env` 生效，无需启用 EnvFile 插件。

在另一个终端运行 `npm run build:extension`，在浏览器扩展管理页加载 `apps/extension/dist`。发布构建使用 `npm run build:extension:public`。

使用聊天配置时，另开终端运行 `.\start-configuration-local.cmd` 与 `.\start-configuration-web.cmd`。在扩展点击“采集并打开配置工作台”，自动跳到 [本地配置 Web](http://127.0.0.1:5174/)，再选择范围并聊天。Web 包位于 `apps/configuration-web`，由独立 Vite 进程提供；后端只提供 API。更新本轮代码后需重启配置后端、重载构建后的扩展并刷新业务页。

Windows 一键发布打包入口（在仓库根目录执行）：

```powershell
.\package-server.cmd
.\package-extension.cmd
# 本地后端版本的插件：.\package-extension.cmd -Local
```

后端包是 `.tmp/server-release/forj-review-server.tar.gz`，公网插件是 `.tmp/forj-review-extension-public.zip`。服务器脚本和 Compose 在打包生成的 `.tmp/server-deploy/`，首次手动上传一次。日常流程：本机打包 → 服务器 `./stop-review.sh` → 手动覆盖后端包 → `./start-review.sh`。插件包在使用者电脑更新。完整步骤与回滚见[部署指南](docs/指南/部署.md)，日志位置与字段见[日志与排查](docs/参考/日志与排查.md)。

**想理解系统**

- [现有业务完整流程与复现指南](docs/参考/现有业务完整流程与复现指南.md) ★ 全业务操作、前后端、采集、异常与 AI 复现
- [一次审核的完整链路](docs/概念/一次审核的链路.md) ★ 从这里开始
- [系统架构与模块边界](docs/概念/系统定位.md)
- [审核流水线、并发与任务生命周期](docs/概念/审核流水线.md)
- [规则主图、能力与子图](docs/概念/规则主图与能力.md)
- [字段与判定规则](docs/概念/字段与判定规则.md)

**想动手**

- [新增审核业务与页面](docs/指南/新增业务与页面.md) ★ 加业务照着做
- [改动与发布准则](docs/指南/改动与发布准则.md)
- [腾讯云部署](docs/指南/部署.md)

**想查事实**

- [术语表](docs/参考/术语表.md)
- [审核工作台展示规范](docs/参考/工作台展示.md)
- [日志与排查](docs/参考/日志与排查.md)
- [各地区业务规则](docs/业务/)
- [GitHub 协作规范](CONTRIBUTING.md)
- [安全边界](SECURITY.md)

## 快速验证

后端：

```powershell
# 在仓库根目录执行
uv run ruff check src tests scripts
uv run python -m compileall -q src
uv run pytest tests -q
uv run python scripts/ci/check_architecture.py
```

前端：

```powershell
npm --prefix apps/extension install
npm --prefix apps/extension test
npm --prefix apps/extension run lint
npm --prefix apps/extension run build
npm --prefix apps/extension run build:public
node scripts/ci/check-extension-architecture.mjs
npm run test:configuration
npm run lint:configuration
npm run build:configuration
```

扩展构建产物位于 `apps/extension/dist`，Web 产物位于 `apps/configuration-web/dist`。不要直接编辑 `dist`，不要恢复旧的 `apps/extension/public/*.js` 双源码。两个前端仅共享 `packages/configuration-contracts` 的纯类型，不互相导入实现。

## 运行时关系

扩展与后端之间只传递版本化审核协议。页面上的字段名称、CSS 选择器和控件操作属于扩展；材料是否齐全、字段是否一致、地区政策是否满足以及是否需要人工复核属于后端。新增功能时先判断它属于哪一侧，再沿注册表和标准协议接入，避免出现同一规则在两端各写一份。

## 开发入口

两个前端均使用 Vue 3、TypeScript 和 Vite，分别在 `apps/extension` 和 `apps/configuration-web` 独立安装依赖、启动和构建。界面统一使用 `.vue` 单文件组件与组合式 API，普通逻辑使用 `.ts`，不使用 React 或 JSX。两端样式、Logo、组件和运行状态分别维护，仅共享纯协议类型。

后端入口通常从 `src/forj_review_assistant/main.py` 和 API 路由进入，审核流程从 `src/forj_review_assistant/runtime/workflow/graph.py` 进入；业务 Profile 位于 `src/forj_review_assistant/businesses`，能力和规则位于 `src/forj_review_assistant/capabilities`、`src/forj_review_assistant/evidence/comparison` 与 `src/forj_review_assistant/presentation`。前端入口从 `apps/extension/src/main.ts` 开始，页面采集查看 `apps/extension/src/platform/browser`，工作台查看 `apps/extension/src/features/review/ui`，页面写回查看 `apps/extension/src/platform/extension`。遇到不确定的代码归属，先阅读 [系统架构与边界](docs/概念/系统定位.md) 和模块 README。

`apps/extension/src/platform/browser/adapters`（页面适配器）已经接入主流程：`apps/extension/src/platform/browser/identity/business-detector.ts` 通过 `buildPageAdapterRegistry()`（`apps/extension/src/platform/browser/adapters/index.ts`）解析当前页面。普通新增页面通过后端页面目录和采集清单接入，复用通用采集与写回能力；超出现有 DOM 采集能力的特殊控件，再由开发人员扩展浏览器平台。适配器只负责页面身份识别，不执行审核规则。

## 交付标准

一个改动只有在代码、测试、文档和回滚方式都明确时才算完成。不能以“本地页面看起来正常”代替后端契约测试，也不能以“测试通过”代替 Profile、注册表和权限边界审查。
