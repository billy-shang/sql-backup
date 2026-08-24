# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.40`）

## 功能

- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- 恢复向导：从完整备份恢复到指定库名（可覆盖）
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  
连接里的备份目录填 SQL Server 本机路径；请打开子目录查看文件。

## 说明（v1.0.40）

备份后会做 `RESTORE VERIFYONLY`，文件校验不过不算成功。差异备份会先确认已有完整备份，SIMPLE 库不能做日志备份。同一连接的手动和定时不会同时跑。飞书 / 企微 / 钉钉改成一次任务一条汇总。

超过 32MB 的 `.bak` 上传群晖改为分块读取，不再整包走 OPENROWSET。容器重启后会补跑 36 小时内漏掉的定时任务。备份前检查 Windows 备份盘剩余空间。历史记录分页；可浏览 SQL Server 上的库/日期/文件。

管理员可从成功记录或目录里的 `.bak` 恢复数据库：先读备份头，再 `RESTORE DATABASE`。只支持完整备份；目标库名可与原来不同。覆盖已有库会先踢掉连接。`.bak` 必须在目标 SQL Server 本机可见。差异 / 日志链恢复、恢复历史记录尚未做。

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`，登录后立刻改密。

## Docker

```bash
docker pull billyshang/sql-backup:v1.0.40
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.40
```

`data/` 必须整目录持久化（SQLite 与 `secret.key` 不能拆开）。群晖等旧 Docker 请用 `v1.0.40`（linux/amd64，关闭 provenance）：

```bash
docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.40 .
```
