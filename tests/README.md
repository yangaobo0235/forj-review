# 测试组织与业务边界

本版测试不需要数据库或 SSH 隧道，不访问真实模型、车型业务接口或原项目数据。测试日志使用临时目录，普通用例隔离私密模型和业务接口配置。

| 目录 | 验证内容 |
| --- | --- |
| `unit/businesses` | 四个业务页面规则、政策、材料路由、页面编译及离线回放 |
| `unit/evidence`、`unit/presentation` | 证据、独立字段比较、工作台任务及回填保护 |
| `unit/capabilities`、`unit/runtime` | 审核图、并发队列、任务期限、生命周期 |
| `unit/integrations` | 模型/二维码/车型 API 和精确 JSON 排放查询 |
| `integration` | 审核 HTTP 与完整业务场景，真实内置 JSON 启动 |
| `contracts` | 请求、响应、错误分类与环境参数 |
| `release`、`tooling` | 发布包校验、部署回退和架构边界 |
| `fixtures`、`support` | 合成材料、固定页面声明及替身；不是生产默认数据 |

`integration/test_file_runtime.py` 核对导出摘要与数据库有效规则指纹，验证所有内置页面、真实版本采集清单和审核入口，不靠合成夹具冒充生产启动。缺失页面和损坏排放数据必须启动失败。`unit/integrations/test_engine_emissions.py` 验证完整型号精确匹配、正常未命中与读取错误区分、不可变数据及多线程共享。

`unit/businesses/test_page_compiler.py` 和 `test_offline_tools.py` 保留原配置模块中的审核内核验证；配置助手、发布事务和数据库迁移测试随已移除功能删除。其余业务用例保持固定预期，不为测试通过修改政策。

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run python scripts/ci/check_architecture.py
npm run test:extension
npm run lint:extension
npm run test:extension:browser
```

扩展浏览器用例覆盖页面采集、身份失效、品牌型号人工回填、车型 ID 写入，以及五条滚动、证据与原图合卡、首行回填及人工输入交互。部署测试使用模拟 Docker 主机测试真实 Bash 脚本；`scripts/ci/smoke_container.py` 对实际镜像禁网验证，不启动数据库，不调用模型。

真实模型成功率、官网核验和原业务页面联调需单独验证；模拟测试通过不表示审核通过。新增回归以业务行为和故障边界为主，不复制实现或删除有效保护。
