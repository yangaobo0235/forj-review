# 可运行示例

`page-config/permit.json` 是虚构许可证页面配置；`page-config/回放样例/permit-cases.json` 提供一致、冲突、缺材料和不确定证据的固定回放。

```powershell
uv run forj-review-service --check-pages examples/page-config
uv run forj-review-service --replay-pages examples/page-config --cases examples/page-config/回放样例/permit-cases.json
```

示例由测试和配置服务的只读样例目录使用，不是实际生产配置，不会自动写入 PostgreSQL。详细契约见[审核配置开发](../docs/审核配置开发.md)。不要在这里存放真实证件、业务值或材料 URL。
