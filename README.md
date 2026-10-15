# forj-review-assistant

FORJ Review Assistant 是一个面向车辆审核页面的浏览器扩展与审核服务。系统通过统一的 LangGraph（流程图编排框架）主图处理材料采集、文档识别、证据聚合、字段比较、业务规则和审核任务生成；前端只负责页面采集、任务呈现和受控页面操作。

当前已配置的审核业务包括青岛和长春的报废置换审核、车源审核和过户审核。一致性审核保留为未配置（`rules_configured=false`）的人工复核入口。

普通页面和字段调整优先使用[声明式页面配置](docs/指南/声明式页面配置.md)：一份 JSON 可新增页面或继承现有版本，字段的提取白名单、材料映射和采集清单由后端生成，前端不增加业务规则。启用前执行 `uv run forj-review-assistant --check-pages review-pages`，再设置 `REVIEW_PAGES_DIR` 并重启服务。本轮仅提供配置底座，不含聊天配置 Agent。

当前先在本地使用；有效配置预览、版本差异、离线回放和历史快照见[本地页面配置工作流](docs/指南/本地页面配置工作流.md)。这些命令不启动服务、不调用模型，也不发布到服务器。

## 文档入口

- [文档总索引](docs/索引.md)
- [项目结构与迁移](docs/指南/项目结构与迁移.md)：目录树、职责、命名、旧路径对照与升级步骤
- [业务底座与遗留审计](docs/参考/业务底座改造与遗留审计.md)：结构判断、已处理耦合及保留的兼容边界

## 安装与启动

在仓库根目录执行，需要 Python 3.11/3.12、uv、Node.js 22.18 或更新版本：

```powershell
uv sync --locked
npm --prefix apps/extension ci
# 首次使用：复制 .env.example 为 .env，在本地填写模型配置
uv run forj-review-assistant --reload
```

在另一个终端运行 `npm run build:extension`，在浏览器扩展管理页加载 `apps/extension/dist`。发布构建使用 `npm run build:extension:public`。

Windows 一键发布打包入口（在仓库根目录执行）：

```powershell
.\package-server.cmd
.\package-extension.cmd
# 本地后端版本的插件：.\package-extension.cmd -Local
```

后端完整发布包在 `.tmp/server-release/`，公网插件在 `.tmp/forj-review-extension-public.zip`。上传后端目录内的全部文件到服务器，再运行 `./start-review.sh`；完整步骤与回滚见[部署指南](docs/指南/部署.md)，日志位置与字段见[日志与排查](docs/参考/日志与排查.md)。

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
```

构建产物位于 `apps/extension/dist`。不要直接编辑 `dist`，不要恢复旧的 `apps/extension/public/*.js` 双源码。

## 运行时关系

扩展与后端之间只传递版本化审核协议。页面上的字段名称、CSS 选择器和控件操作属于扩展；材料是否齐全、字段是否一致、地区政策是否满足以及是否需要人工复核属于后端。新增功能时先判断它属于哪一侧，再沿注册表和标准协议接入，避免出现同一规则在两端各写一份。

## 开发入口

后端入口通常从 `src/forj_review_assistant/main.py` 和 API 路由进入，审核流程从 `src/forj_review_assistant/runtime/workflow/graph.py` 进入；业务 Profile 位于 `src/forj_review_assistant/businesses`，能力和规则位于 `src/forj_review_assistant/capabilities`、`src/forj_review_assistant/evidence/comparison` 与 `src/forj_review_assistant/presentation`。前端入口从 `apps/extension/src/main.tsx` 开始，页面采集查看 `apps/extension/src/platform/browser`，工作台查看 `apps/extension/src/features/review/ui`，页面写回查看 `apps/extension/src/platform/extension`。遇到不确定的代码归属，先阅读 [系统架构与边界](docs/概念/系统定位.md) 和模块 README。

`apps/extension/src/platform/browser/adapters`（页面适配器）已经接入主流程：`apps/extension/src/platform/browser/identity/business-detector.ts` 通过 `buildPageAdapterRegistry()`（`apps/extension/src/platform/browser/adapters/index.ts`）解析当前页面。普通新增页面通过后端页面目录和采集清单接入，复用通用采集与写回能力；超出现有 DOM 采集能力的特殊控件，再由开发人员扩展浏览器平台。适配器只负责页面身份识别，不执行审核规则。

## 交付标准

一个改动只有在代码、测试、文档和回滚方式都明确时才算完成。不能以“本地页面看起来正常”代替后端契约测试，也不能以“测试通过”代替 Profile、注册表和权限边界审查。
