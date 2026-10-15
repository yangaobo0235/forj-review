# 页面配置目录

在这里放置经过校验的页面 `.json` 配置，通过环境变量 `REVIEW_PAGES_DIR=review-pages` 启用。
服务启动时整体加载，不会自动监听文件变化。每个 `page_id` 只能有一个 `active=true` 版本。

编写方式见 [页面配置指南](../docs/指南/声明式页面配置.md)，可复制 [示例](../docs/示例/页面配置/) 后修改。
不要放入真实单据、图片地址或识别结果。

本地操作见[本地页面配置工作流](../docs/指南/本地页面配置工作流.md)：

```powershell
uv run forj-review-assistant --check-pages review-pages --json
uv run forj-review-assistant --inspect-pages review-pages
uv run forj-review-assistant --snapshot-pages review-pages
```

最后一条命令在 `.history/` 保存本地基线；此后同一身份和版本的内容不能覆盖，活动标记仍可切换。回放样例放在子目录或独立目录，根目录的 `.json` 只存页面声明。
