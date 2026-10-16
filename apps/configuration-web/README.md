# 审核配置 Web

独立 Vue 3/TypeScript/Vite 前端，负责配置范围、聊天、规则依据、验证和确认。后端执行推理、配置编辑、校验与保存；此包不计算审核业务规则。

在仓库根目录运行：

```powershell
npm --prefix apps/configuration-web ci
.\start-configuration-local.cmd
# 另一个终端启动前端
.\start-configuration-web.cmd
```

默认地址为 `http://127.0.0.1:5174/`。`/api` 和 `/health` 同源代理到配置服务 `http://127.0.0.1:8011`。可将本包 `.env.example` 复制为 `.env` 设置 `CONFIGURATION_API_URL`，启动进程同名变量优先；只配置代理目标，不填写模型密钥。需要隔离测试时，也可以在启动 Vite 的进程环境设置 `CONFIGURATION_API_URL`；浏览器扩展的正式工作台桥固定使用本机 5174。

从扩展点击“采集并打开配置工作台”，Web 根据采集 ID 从服务读取持久快照。只读试采集通过 `browser/bridge.ts` 委托扩展在原业务标签页执行。两前端仅共享 `@forj/configuration-contracts` 的纯类型，不互相导入实现。

目录：`app` 装配与样式，`features` 界面，`model` 会话与展示状态，`api` 服务协议，`browser` 扩展桥，`ui` 通用图标。

验证命令：`npm run test:configuration`、`npm run lint:configuration`、`npm run build:configuration`。完整启动、恢复和限制见[使用指南](../../docs/指南/聊天审核配置Agent.md)。
