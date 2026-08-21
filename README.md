# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.35`）

## 功能

- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  
连接里的备份目录填 SQL Server 本机路径；请打开子目录查看文件。

## 说明（v1.0.35）

表格改为固定列宽：长文本省略、鼠标悬停看全文，操作按钮单行排列，页面不再出现横向滚动条。类型、用户名、创建时间等次要列已收进详情或合并显示。

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`，登录后立刻改密。

## Docker

```bash
docker pull billyshang/sql-backup:v1.0.35
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.35
```

`data/` 必须整目录持久化（SQLite 与 `secret.key` 不能拆开）。群晖等旧 Docker 请用 `v1.0.35`（linux/amd64，关闭 provenance）：

```bash
docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.35 .
```
