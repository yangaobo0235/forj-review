# Review Assistant

Review Assistant 是一个面向车辆审核页面的浏览器扩展与审核服务。系统通过统一的 LangGraph（流程图编排框架）主图处理材料采集、文档识别、证据聚合、字段比较、业务规则和审核任务生成；前端只负责页面采集、任务呈现和受控页面操作。

当前已配置的审核业务包括青岛和长春的报废置换审核、车源审核和过户审核。一致性审核保留为未配置（`rules_configured=false`）的人工复核入口，不等同于「停用」；`TRANSFER_LEGACY`（`version="deprecated"`）只服务历史数据迁移、永不进 `BUSINESS_PROFILES`，它不代表过户业务停用。

## 文档入口

- [文档总索引](docs/索引.md)

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
cd review-agent-service
uv run ruff check app tests
uv run python -m compileall -q app
uv run pytest tests -q
```

前端：

```powershell
npm --prefix review-extension install
npm --prefix review-extension test
npm --prefix review-extension run lint
npm --prefix review-extension run build
```

构建产物位于 `review-extension/dist`。不要直接编辑 `dist`，不要恢复旧的 `review-extension/public/*.js` 双源码。

## 运行时关系

扩展与后端之间只传递版本化审核协议。页面上的字段名称、CSS 选择器和控件操作属于扩展；材料是否齐全、字段是否一致、地区政策是否满足以及是否需要人工复核属于后端。新增功能时先判断它属于哪一侧，再沿注册表和标准协议接入，避免出现同一规则在两端各写一份。

## 开发入口

后端入口通常从 `app/main.py` 和 API 路由进入，审核流程从 `app/workflow/graph.py` 进入；业务 Profile 位于 `app/businesses`，能力和规则位于 `app/capabilities`、`app/compare` 与 `app/presentation`。前端入口从 `src/main.tsx` 开始，页面采集查看 `src/browser`，工作台查看 `src/components`，页面写回查看 `src/session`。遇到不确定的代码归属，先阅读 [系统架构与边界](docs/概念/系统定位.md) 和模块 README。

`src/adapters`（页面适配器）已经接入主流程：`src/browser/business-detector.ts` 通过 `buildPageAdapterRegistry()`（`src/adapters/index.ts`）解析当前页面，`ScrapReplacementPageAdapter` 等适配器负责页面识别与业务判定。采集和写回仍在 `src/browser` 内实现，新增页面时在 `src/adapters/` 新增一个适配器并在 `buildPageAdapterRegistry()` 中注册。

## 交付标准

一个改动只有在代码、测试、文档和回滚方式都明确时才算完成。不能以“本地页面看起来正常”代替后端契约测试，也不能以“测试通过”代替 Profile、注册表和权限边界审查。
