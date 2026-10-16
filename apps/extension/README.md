# FORJ Review Assistant 浏览器扩展

浏览器扩展负责页面采集、审核工作台呈现和受控写回。它通过后端协议获得事实与任务，不实现服务端业务规则。

其中 `PageAdapter`（页面适配器）负责网页采集，`Renderer`（渲染器）负责工作台展示，`PageAction`（页面动作）负责受控写回；`DOM`（页面文档对象模型）只允许在浏览器侧访问。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `apps/extension/src/app`、`entrypoints` | 侧栏装配、样式和扩展入口 |
| `apps/extension/src/features/review/api` | 审核请求、轮询和页面采集编排 |
| `apps/extension/src/features/review/presentation` | 证据、差异和异常的展示转换 |
| `apps/extension/src/platform/browser` | DOM 读取、页面识别、图片候选和浏览器 API |
| `apps/extension/src/platform/browser/adapters` | 页面到 `PageData` 的适配器 |
| `apps/extension/src/platform/extension` | 会话、页面动作控制和动作注册表 |
| `apps/extension/src/features/review/ui` | Vue 工作台和任务渲染器 |
| `apps/extension/src/features/review/model` | 工作流状态和请求编排 |
| `apps/extension/src/shared/contracts` | 前端协议和页面动作类型 |
| `public/manifest.json` | 扩展权限与入口声明 |
| `dist` | 构建产物，禁止手工编辑 |

## 页面扩展规则

新增审核页面先添加 `PageAdapter` 和 `PageActionRegistry` 条目，再接入通用采集与工作台。页面选择器、字段定位、写回白名单只能存在适配器或动作控制器中；不要把 DOM 选择器放进后端或 Vue 业务组件。

## 本地验证

```powershell
npm ci
npm test
npm run lint
npm run build
```

普通 `npm run build` 生成连接本地 `127.0.0.1:8010` 的开发版，扩展名称会显示为“赋界资料审核工作台(本地)”；`npm run build:public` 生成连接腾讯云审核服务的朋友试用版，扩展名称保持为“赋界资料审核工作台”。需要临时连接其他环境时，可将本包 `.env.example` 复制为 `.env`，或在构建进程中设置 `VITE_AGENT_BASE_URL`。本地无需填写模型密钥；前端不保存后端模型凭据。

## 前端运行链路

扩展一次调用 POST /api/review/jobs/urls，按 GET /api/review/jobs/{job_id} 轮询。普通材料字段仍由模型直接读取 URL；二维码官网核验在模型没有可靠 `qr_codes` 时，由后端对受控候选图片 URL 读取一次原图到内存，ZXing 优先、OpenCV 兜底，再执行官网域名、HTTPS、访问及字段核验。二维码读取不会写盘、上传或重复调用模型；工作台会显示二维码来源和失败阶段，无法可靠识别或官网不可用时转人工复核。

结果缩略图使用原图地址；模型无法访问的地址和 `blob:` 等浏览器内地址显示为人工复核，不自动回退到下载或上传。图片上传接口及本地处理代码已删除。Renderer 只负责可视化和用户确认；Action Controller 在执行前重新确认页面实例、字段原值和动作权限。任何网络错误、协议错误或页面变化都应显示为可恢复状态，不能静默清空审核结果。完整生命周期见[审核流水线文档](../../docs/概念/审核流水线.md)。

重新加载或更新扩展后，已经打开的业务网页不会自动获得新 Content Script。出现 `Could not establish connection. Receiving end does not exist.` 时，应刷新业务网页并重新打开侧边栏，不要把它误判为后端故障。

所有字段工作台固定展示“待处理、全部字段、页面外核验”三个分类，不展示顶部最终建议大卡片或材料完整性字段、页签、卡片。后端不再生成材料展示任务；旧响应中的材料任务在统计和选中任务前统一排除，计数以可见任务为准。后端材料检查、诊断报告和最终建议保留。回填通过浏览器消息直接写页面，不调用模型或后端。回填守卫只实时采集身份锚点，并复用单次校验样本，避免反复构建整个字段目录；别名索引随业务清单替换更新。页面身份、原值、唯一性、写后回读和回滚检查继续保留。

## 代码评审重点

检查组件是否只消费后端结果、组合式函数是否避免重复请求、Adapter 是否处理异步 DOM 和页面刷新、Action 是否执行白名单与回读、Manifest 是否只申请必要权限、类型是否与后端协议一致。禁止直接改 `dist`、在 `public` 新增旧式业务脚本，或在 UI 层复制后端规则。
