# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.39`）

## 功能

- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  
连接里的备份目录填 SQL Server 本机路径；请打开子目录查看文件。

## 说明（v1.0.39）

SSH 只是跳板机时，看不到 Windows 的 `G:` 盘，空目录改由 SQL 删除：临时打开 `xp_cmdshell`（需要 `sysadmin`），删完立刻关回去。跳板机上的 `sudo` 帮不上忙。

## 说明（v1.0.38）

Windows 上过期日期文件夹现在会一起删掉。`.bak` 仍用 SQL 的 `xp_delete_file`；空目录改走 SSH（`rmdir`），不再依赖已关闭的 `xp_cmdshell` / OLE。`sysadmin` 解决不了这个问题。群晖侧目录已存在时不再刷 code=400。

## 说明（v1.0.37）

保留天数现在会真正删除过期备份：SQL Server 本机的 `{目录}\\{库名}\\{日期}`，以及群晖对应日期目录。例如保留 2 天 = 只留今天和昨天。

## 说明（v1.0.36）

操作列加宽，备份/编辑/删除都能点到；大小、时间、状态不再挤到换行。长文本仍省略，鼠标悬停看全文。

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`，登录后立刻改密。

## Docker

```bash
docker pull billyshang/sql-backup:v1.0.39
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.39
```

`data/` 必须整目录持久化（SQLite 与 `secret.key` 不能拆开）。群晖等旧 Docker 请用 `v1.0.39`（linux/amd64，关闭 provenance）：

```bash
docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.39 .
```
