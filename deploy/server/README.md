# 首次部署固定文件

本目录直接保存首次部署所需的脚本、Compose 和无密码模板，无需生成。日常打包只输出 `release/server/forj-review-server.tar.gz`，不更新本目录。

首次安装或首次升级关系存储版本时，将本目录内的文件与后端压缩包一起上传到服务器 `/home/ubuntu/forj-review/`，不要多套一层 `server` 目录。在服务器执行：

```bash
cd /home/ubuntu/forj-review
bash setup-server.sh
```

填写生成的 `.env` 中的模型 API Key，以及 `.env.database` 中现有 `postgres` 账号的密码。也可单独上传已在本机填写好的服务器专用配置；已有文件不会被初始化覆盖，真实配置被 Git 忽略，不进入后端包。

线上数据库默认使用 `forj_review.public`，本机使用独立 `local`；确认容器 `postgres` 已加入 `postgres_network`。`postgres/` 仅提供全新实例的安装模板，现有数据库无需重装。

新后端启动前必须完成对应 Schema 的结构迁移。旧 public 首次升级请按项目 `docs/运维/部署运维.md` 中“关系配置版本的首次部署”执行；启动脚本只预检，不自动迁移。然后启动并验证：

```bash
./start-server.sh
curl --fail http://127.0.0.1:26101/health
```

日常只需要：

```bash
cd /home/ubuntu/forj-review
# 上传并覆盖 forj-review-server.tar.gz；不提前停止旧服务
./start-server.sh
```

脚本、私密配置及数据库继续保留。完整安装、网络、日志和回滚流程见项目中的 `docs/运维/部署运维.md`。
