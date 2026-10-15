# FORJ Review Assistant 后端

后端审核服务，负责接收页面采集的 `ReviewRequest`，选择 Profile，运行统一 LangGraph，并返回版本化的 `ReviewTask`、证据事实和建议。浏览器扩展不应复制这里的业务判断。

其中 `ReviewRequest`（审核请求）是前端提交的数据，`Profile`（业务配置档案）决定业务组合，`LangGraph`（流程图编排框架）负责生命周期，`ReviewTask`（审核任务）是返回给审核员处理的事项。

## 目录职责

以下路径相对于本 Python 包；验证命令在仓库根目录执行。

| 目录 | 职责 |
| --- | --- |
| `interfaces/` | HTTP 路由、鉴权、错误映射与 CLI |
| `bootstrap/` | 应用创建、服务装配与生命周期 |
| `config/`、`contracts/` | 配置和请求、响应、证据、识别协议 |
| `businesses/` | 业务声明、Profile、材料策略与业务规则 |
| `capabilities/` | 能力契约、注册表、Handler 与通用子图 |
| `runtime/` | 审核服务、作业、公平调度和 LangGraph 编排 |
| `evidence/` | 字段归一化、取值、比较与聚合 |
| `presentation/` | 任务展示、步骤路由、建议和响应装配 |
| `integrations/` | 模型识别、二维码解码和官网核验 |
| `observability/` | 日志配置 |

业务声明集中在 `businesses/packs/<业务>.py`，执行流程消费 Profile 和注册表，不在 HTTP 或调度器中添加业务分支。测试位于仓库根目录 `tests/`。

## 异步审核接口

扩展一次调用 POST /api/review/jobs/urls，按 GET /api/review/jobs/{job_id} 轮询。普通材料字段仍由模型直接读取 URL；二维码官网核验在模型没有可靠 `qr_codes` 时，对受控候选图片 URL 读取一次原图到内存，ZXing 优先、OpenCV 兜底，然后执行官网域名、HTTPS、访问及字段核验。二维码读取不会写盘、上传或重复调用模型；每次尝试都会记录来源和失败阶段，无法可靠识别或官网不可用时明确转人工复核。

当前本地默认限制为：单次最多 16 张、单任务模型识别 6、全服务模型识别 12、最终汇总 2；生产配置为单任务 8、全服务 16。最终汇总复用单图识别得到的 `AgentBatchResult`，不得重复调用图片模型。具体状态、阶段、清理规则和环境变量见[审核流水线文档](../../docs/概念/审核流水线.md)。

任务只保留图片地址和识别结果，快照约 10 分钟后过期。二维码读取的原图字节只在解码调用内存中存在；原生解码若超时，线程结束前仍持有数据和并发槽位。取消会停止排队和运行中的协程，完成后的任务保留终态结果，不被取消覆盖。

## 开发约束

- 业务差异进入 `src/forj_review_assistant/businesses/profiles.py` 和注册表，不在节点中写业务 `if`。
- 新能力必须有 `CapabilitySpec`、Handler、测试和可追踪的 `CapabilityResult`。
- 统一主图只编排生命周期；业务分支由 Profile 和 Planner 数据驱动。
- 对外协议优先使用 `review_tasks`、`EvidenceFact` 和 `CheckResult`，旧 `review_steps` 仅可在兼容层转换。

## 本地验证

```powershell
uv run ruff check src tests scripts
uv run python -m compileall -q src
uv run pytest tests -q
```

## 请求处理约定

API 层只负责鉴权、协议解析、请求 ID 和错误映射；审核服务负责组装 LangGraph 输入；节点负责生命周期阶段；规则和能力负责领域判断。任何层都不得跳过下一层直接调用数据库、浏览器或模型客户端。异常要转换为稳定错误码，内部堆栈只写入受控日志。

## 新增后端代码前的检查

先确认是否已有同类字段、能力或注册表条目；优先扩展现有模型和接口。新增公开字段要更新 `src/forj_review_assistant/contracts`、前端类型、契约测试和文档。新增规则要说明证据来源、缺失行为、冲突行为、版本和 Profile 范围。新增外部调用要声明超时、重试、熔断和脱敏策略。
