# 独立审核版服务器文件

详细流程见[部署运维](../../docs/运维/部署运维.md)。

首次将本目录全部固定文件及 `release/server/forj-review-audit-server.tar.gz` 上传到 `/home/ubuntu/forj-review-audit/`，执行 `bash setup-server.sh`，填写 `.env` 后执行 `./start-server.sh`。无需 PostgreSQL、SQLite、数据库密码或 SSH 隧道。

日常更新上传新压缩包，再运行 start；停止用 `./stop-server.sh`，恢复上一镜像和规则用 `./rollback-server.sh`。脚本有变更时同步上传。默认 26101，镜像/容器/Compose 项目使用 `forj-review-audit` 独立名称，与原服务并行需另外配置端口及扩展地址。

`.env`、`.runtime`、logs 和部署日志留在服务器，不上传到 Git。规则与排放参考在镜像内；更新须在源码验证后重新打包。启动前验证新镜像数据，异常不替换当前运行服务。
