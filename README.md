# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.47`）

## 功能

- 配置中心：远程备份（群晖）和 SSH 代理统一维护；连接里下拉选用
- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- 恢复向导：从完整备份恢复到指定库名（可覆盖）
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  
连接里的「本地备份目录」填 SQL Server 本机路径；请打开子目录查看文件。

管理员在侧栏「配置中心」维护两类共用配置：

- 远程备份：群晖地址、账号、远程目录。连接勾选「是否远程备份」后下拉选用。
- SSH 代理：跳板地址、账号、密码或私钥。连接方式选 SSH 后下拉选用，不再在连接里手填。

删除仍被连接使用的 SSH 代理会被拒绝；删除群晖会使已选用的连接关闭远程备份。  
新增或编辑连接时，可在下拉旁直接「新增」群晖或 SSH 代理，不必离开当前表单。群晖归档失败时，备份页「更多」里可重试。

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`。先到「配置中心」添加群晖和 SSH 跳板，再在「数据库连接」里下拉选用。旧连接里手填的 SSH 会在升级后自动收进配置中心。

## Docker

```bash
docker pull billyshang/sql-backup:v1.0.47
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.47
```

`data/` 必须整目录持久化（SQLite 与 `secret.key` 不能拆开）。群晖等旧 Docker 请用 `v1.0.47`（linux/amd64，关闭 provenance）：

```bash
docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.47 .
```


