# FORJ 审核辅助系统（独立审核版）

本版本只包含审核后端和浏览器扩展，支持青岛/长春报废置换、车源审核及过户审核。不包含审核配置管理页面、配置助手或数据库服务；运行和部署均不需要 PostgreSQL、SQLite 或 SSH 隧道。

审核规则和排放参考数据由原项目经 IDEA SSH 隧道从 `forj_review.local` 的活动发布及 `ref_engine_emission` 只读导出。内置数据位于 `src/forj_review_service/data/`；`source.json` 记录来源、导出时间、版本、数量和校验摘要，绝不以测试夹具初始化生产数据。

## 本地运行

需要 Python 3.11/3.12、uv、Node.js 22.18+。在本目录执行：

```powershell
uv sync --locked
Copy-Item src/forj_review_service/.env.example src/forj_review_service/.env
# 已有 .env 时保留原文件；填写模型密钥后启动。
uv run forj-review-service --host 127.0.0.1 --port 26101
```

另开终端构建扩展：

```powershell
npm --prefix apps/review-extension ci
npm run build:extension
```

在 Chrome/Edge 扩展管理页启用开发者模式，加载 `apps/review-extension/dist`。业务页面中打开审核辅助侧栏即可使用。车型搜索需要在后端 `.env` 配置原业务车型接口；未配置时明确提示，不伪造车型候选。

页面规则和排放 JSON 在启动时完整校验并固定于内存；无效或缺失时启动失败。文件变更后重启服务。审核任务与短期识别结果只在内存保存，重启后消失，不保存原图。

## 验证及打包

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/ci/check_architecture.py
npm run test:extension
npm run lint:extension
npm run test:extension:browser
npm run build:extension:public
.\scripts\release\package-server.cmd
.\scripts\release\package-extension.cmd
```

后端包输出 `release/server/forj-review-audit-server.tar.gz`，首次部署固定文件在 `deploy/server/`，默认服务器目录为 `/home/ubuntu/forj-review-audit/`。镜像、容器和 Compose 项目均使用独立名称，规则及参考 JSON 随镜像发布和回滚。默认端口 26101；与原服务并行部署时须另设端口并为扩展设置对应服务地址。

详细说明见[文档导航](docs/README.md)、[本地开发](docs/开发/本地开发.md)、[部署运维](docs/运维/部署运维.md)、[开发规范](docs/开发/开发规范.md)。
