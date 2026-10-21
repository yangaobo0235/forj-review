# GitHub 协作与提交规范

需求分析、编码、配置维护、验证和 AI 辅助开发统一遵循[开发规范](../docs/开发/开发规范.md)。本文负责分支、提交和评审。

本文使用的 `PR`（Pull Request，拉取请求/合并请求）、`Issue`（问题单）、`CI`（持续集成）和 `Conventional Commits`（约定式提交）含义见[英文术语对照表](../docs/开发/项目结构.md)。

## 分支

- 日常开发分支使用 `codex/<topic>` 或 `feature/<topic>`。
- `main` 只接收经过验证的合并提交。
- 一个分支只解决一个完整主题；架构迁移、业务规则和纯文档变更不要混在同一个提交中。

## 提交消息

使用 Conventional Commits：

```text
<type>(<scope>): <imperative summary>
```

允许的 `type`：`feat`、`fix`、`refactor`、`test`、`docs`、`build`、`chore`、`perf`、`security`。

示例：

```text
feat(profile): register changchun invoice policy capability
fix(extension): reject stale page fill intent
docs(architecture): describe capability subgraph contract
```

标题使用英文动词开头，长度建议不超过 72 个字符。正文说明问题、行为变化、验证命令和已知限制。不要在提交中写“修改若干问题”这类无法审查的描述。

## Pull Request

PR 必须包含：

1. 问题和触发场景；
2. 变更后的行为；
3. 影响的模块和协议；
4. 测试命令及结果；
5. 是否修改 Profile、能力注册表、主图或前后端边界；
6. 迁移、兼容和回滚方式。

涉及以下内容时必须请求架构复核：

- 修改 LangGraph 节点拓扑或状态字段；
- 修改 `ReviewRequest`、`ReviewResponse`、`ReviewTask`、`EvidenceFact`；
- 新增或停用生产业务 Profile；
- 改变能力依赖、失败策略或页面写入权限；
- 让前端实现业务审核规则，或让后端访问浏览器 DOM。

## 合并前检查

- 已按开发规范明确职责归属、追溯根因并在责任层修复；跨层变更同步契约和消费者，验证原场景及受影响链路，没有以绕过校验或特例掩盖问题。
- 已清理本次变更替代的旧实现、死代码、无效配置、失效引用和临时内容；保留的兼容逻辑有实际用途与明确原因。
- `git diff --check`
- 后端 Ruff、compileall、pytest
- 前端 test、lint、build
- 文档链接和目录结构检查
- 不包含密钥、真实身份证号、真实图片、临时日志和构建缓存

## Issue

Issue 标题要包含领域和结果，例如：`[Capability] invoice policy does not produce a review task`。正文必须给出复现输入、期望行为、实际行为和日志中的 `trace_id`（如有）。安全问题不要公开创建 Issue，参见 [SECURITY.md](SECURITY.md)。

## 提交拆分建议

协议或架构迁移、业务规则变更、前端适配和文档可以在同一功能分支中协同完成，但提交应按可审查的逻辑拆分：先提交模型与兼容层，再提交能力或页面实现，最后提交测试和文档。不要把格式化、无关重命名和业务改动混在一起，否则无法判断行为变化。

## PR 评审顺序

评审者先看协议和边界，再看注册表与主图影响，然后看规则实现，最后看 UI 和样式。作者应在 PR 描述中明确回答：是否新增状态或枚举、是否改变默认降级、是否扩大页面权限、是否改变自动通过条件、旧客户端如何处理、如何回滚。

## 版本兼容

可选字段优先向后兼容；删除字段、改变枚举含义、改变任务 ID 生成方式或改变页面动作语义都属于破坏性变化。破坏性变化必须提供转换层、兼容窗口和迁移说明，不能仅依赖前端和后端同时上线来掩盖协议不兼容。
