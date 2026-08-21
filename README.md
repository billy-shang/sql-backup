# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.34`）

## 功能

- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  
连接里的备份目录填 SQL Server 本机路径；请打开子目录查看文件。

## 说明（v1.0.34）

- 备份进度条改为浅绿色；鼠标悬停可看详细状态
- 备份 + 群晖上传期间页面轮询不再把 SQLite 锁死（WAL、进度接口不查库、上传前先提交事务）
- Linux 容器上传群晖时文件名只取 `.bak` 名，不会把 `D:\sql_backup\...` 整段拼进远程路径
- 进度轮询遇到瞬时错误不再误报失败；群晖 HTTPS 开关会自动在 5000/5001 之间切换端口

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`，登录后立刻改密。

## Docker

```bash
docker pull billyshang/sql-backup:v1.0.34
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.34
```

`data/` 必须整目录持久化（SQLite 与 `secret.key` 不能拆开）。群晖等旧 Docker 请用 `v1.0.34`（linux/amd64，关闭 provenance）：

```bash
docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.34 .
```
