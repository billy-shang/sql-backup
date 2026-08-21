# SQL Backup — SQL Server 备份管理平台

面向运维人员的 **SQL Server 自动备份管理** 系统。平台可以部署在任意机器；配置好数据库连接后，由 SQL Server **自己在所在服务器上** 执行 `BACKUP DATABASE`，`.bak` 落在数据库服务器本地路径（默认是实例 Backup 目录，一般在 SQL Server 安装目录下）。

Web 管理后台为 **Vue3 + Element Plus**。

- 源码：https://github.com/billy-shang/sql-backup
- Docker 镜像：https://hub.docker.com/r/billyshang/sql-backup

## 功能

| 模块 | 说明 |
|------|------|
| 登录与权限 | 管理员：全部权限；普通运维：查看、手动执行备份、下载；不能改连接/策略/用户/通知配置 |
| 数据库连接 | 多实例：名称、类型、地址、端口、库名、账号、直连/SSH；可配置群晖远程备份，本地备份完成后再上传 |
| 备份执行 | 平台只下发 `BACKUP DATABASE`。文件写在 **SQL Server 所在服务器**，不是平台所在电脑。直连或 SSH 隧道均可；无 ODBC 时自动用纯 Python TDS |
| 备份策略 | 完整 / 差异 / 日志；保留天数；压缩；是否删除过期文件 |
| 定时任务 | 每天 / 每周 / 指定时间；状态：运行中、成功、失败、暂停 |
| 文件管理 | 库名、时间、大小、状态、路径；下载、删除、手动备份 |
| 通知 | 飞书机器人卡片：连接名、地址、库名、时间、备份路径；成功绿色、失败红色 |

备份文件始终写在 **SQL Server 所在那台服务器** 上（`BACKUP ... TO DISK` 的路径是数据库引擎本机路径）：

```
{服务器备份目录}\{数据库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak
留空目录时：SQL Server 安装目录下的默认 Backup，例如
  C:\Program Files\Microsoft SQL Server\MSSQL10_50.MSSQLSERVER\MSSQL\Backup\ERP\2026-08-20\ERP_....bak
指定目录时：该服务器上的路径，例如 D:\TEST\ERP\2026-08-20\ERP_....bak
Linux 示例：/var/opt/mssql/data/ERP/2026-08-20/ERP_....bak
```

连接里的「服务器备份目录」填 **数据库服务器自己的路径**，不要填平台路径、也不要填 SSH 跳板机路径。留空 = 用 SQL Server 实例默认 Backup 目录。

若出现「操作系统错误 3 / 找不到指定的路径」，说明 SQL Server 机器上还没有该目录，或服务账号没有写入权限。请先在 **192.168.0.2 这类数据库服务器** 上创建 `D:\sql_backup`（资源管理器或 `mkdir`），并给 SQL Server 服务账号写入权限；平台账号执行建目录通常需要 sysadmin。群晖容器里的 python-tds 已改为逐级 `xp_create_subdir`，不再用会报「参数无效」的参数化调用。

平台不会把 Windows 实例的 `.bak` 拷到 `data/backups/`。网页「下载」在跳板机看不到该磁盘时不可用，请到数据库服务器上看文件。**请打开子目录**，例如 `D:\TEST\HqTemp\2026-08-20\`，根目录 `D:\TEST` 下面不会直接出现 `.bak`。

## 环境要求

- Python 3.12+（平台可装在 Windows / Linux / 任意能跑 Python 的机器上）
- 连接库：优先本机 ODBC 17/18（可选）；没有则自动使用 **python-tds**，不必在平台机器上安装 SQL 客户端
- 直连：平台网络能访问目标 `host:port`（通常 1433）
- SSH：平台能 SSH 到跳板机或库所在机，再隧道到 SQL 端口
- 前端构建（可选）：Node.js 18+（`npm install && npm run build`）
- 飞书通知：自定义机器人 Webhook

## 安装与启动

```bash
cd D:\CURSOR\sql_backup
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt

# 后端（同时托管已构建的前端 dist；未构建时仍提供 API）
python -m app
```

## 管理后台页面

| 页面 | 路径 | 说明 |
|------|------|------|
| 登录 | `/login` | 居中卡片登录：用户名/密码，JWT 会话。顶栏点用户名可改密、查看使用文档；退出在左侧栏底部 |
| 概览 | `/` | 连接数、任务数、成功/失败/运行中、最近备份；可清空日志 |
| 数据库连接 | `/connections` | 增删改、弹窗内测试并勾选库、立即备份；远程备份配置（群晖） |
| 备份文件 | `/backups` | 历史、筛选、下载、删除 |
| 定时任务 | `/schedules` | 每天/每周/指定时间，暂停/恢复 |
| 飞书通知 | `/notify` | Webhook 与成功/失败开关 |
| 用户管理 | `/users` | 仅管理员：创建管理员或普通运维 |

普通运维可见各页面（用户管理除外），可立即备份、下载文件；不能新增/编辑/删除连接、任务、通知与用户。编辑连接时在弹窗内点「测试」列出库名。

默认账号：

- 用户名：`admin`
- 密码：`admin@123`
- 请登录后立即修改密码

开发时前后端分离：

```bash
# 终端 1
python -m app

# 终端 2
cd frontend
npm install
npm run dev
```

前端开发服务器默认 `http://127.0.0.1:5173`，`/api` 代理到 `8788`。

生产构建前端并交给 FastAPI 托管：

```bash
cd frontend
npm install
npm run build
```

产物在 `frontend/dist`，重启 `python -m app` 即可。

## 数据库选择

- 新增/编辑连接时，**数据库名默认为全部用户库，可留空**
- 在弹窗底部点 **测试**：连通成功后列出全部库；**系统库（master / tempdb / model / msdb）会标注「系统库」**，默认不勾选
- 可按需勾选系统库；**仅用户库 / 全不选** 保存后仍按「全部用户库」备份
- 全选会把系统库一并写入配置
- 连接列表的操作列不再提供测试按钮，测试只在新增/编辑弹窗内进行

## 连接方式说明

### 模式 1：数据库直接连接

平台能直接访问 SQL 端口时使用：

```
任意位置的管理平台 --TCP 1433--> SQL Server
执行 BACKUP DATABASE TO DISK='服务器本机路径'
```

`.bak` 出现在 SQL Server 那台机器上。

### 模式 2：SSH 代理

平台访问不到 SQL 端口、只能 SSH 时使用：

```
任意位置的管理平台 --SSH--> 跳板机 --隧道--> SQL Server:1433
执行 BACKUP DATABASE TO DISK='服务器本机路径'
```

例如：平台在你的电脑，SQL 在 `192.168.1.4`，SSH 跳板是 `unique.uniquexm.cn`。文件应出现在 `192.168.1.4` 上配置的目录（或默认 Backup 目录），而不是平台的 `data\backups`。

SSH 可填密码或私钥（二选一，私钥优先）。

## 备份类型

- **完整（Full）**：`BACKUP DATABASE ...`
- **差异（Differential）**：`WITH DIFFERENTIAL`（需已有完整备份）
- **日志（Log）**：`BACKUP LOG ...`（库须为完整恢复模式）

勾选压缩时追加 `WITH COMPRESSION`（SQL Server 版本需支持）。

## 定时任务

每个任务绑定一个数据库连接，并带自己的策略（类型/保留天数/压缩/删旧）。

- 每天：例如 `02:00`
- 每周：选择星期 + 时间
- 指定时间：一次性执行（`once_at`）

暂停任务后调度器不再触发，可再启用。

## 飞书通知

在「通知设置」填写机器人 Webhook。成功时推送绿色卡片，失败时推送红色卡片，字段示例：

```
数据名称：UNIQUE_192.168.1.3
数据地址：192.168.1.3:1433
数据库：GWTTest
时间：2026-08-20 18:27
备份路径：D:\SQL_BACKUP\GWTTest\2026-08-20\GWTTest_20260820_182753_full.bak
```

失败卡片同样包含上述字段，并额外带失败原因。

## 群晖远程备份

在「数据库连接」页点 **远程备份配置**，填写群晖地址、端口、账号、密码和 File Station 目录。

- 地址填群晖域名或内网 IP，例如 `andy.uniquexm.cn`
- HTTP 用端口 **5000**、HTTPS 用 **5001**（与 DSM 控制面板一致）
- 远程目录必须是已存在的共享及其子路径，例如 `/fileserver/DB_BackUP`
- 备份会放到 `远程目录/连接名/库名/日期/` 下，例如 `/fileserver/DB_BackUP/UNIQUE_192.168.1.3/GWTTest/2026-08-20/`

新增/编辑连接时，在「服务器备份目录」下方打开 **是否远程备份**，并选择刚保存的群晖。本地 `.bak` 在 SQL Server 上生成成功后，平台再经 File Station HTTP API 上传。

上传只走 File Station，**不走 SSH/SFTP 22 端口**。公网群晖通常只放行 5000/5001，22 未开放时以前会误报「Unable to connect to port 22」。DSM 账号需要对共享有写入权限；Windows 备份文件会经 SQL 通道读取后上传（单文件建议不超过 512MB）。

## API 摘要

均需 `Authorization: Bearer <token>`（登录除外）。

- `POST /api/auth/login` — `{username, password}` → token、角色
- `GET /api/auth/me` — 当前用户
- `POST /api/auth/password` — 修改自己的密码
- `GET/POST /api/connections` — 连接列表 / 新增（管理员写）
- `PUT/DELETE /api/connections/{id}`
- `POST /api/connections/probe` — 弹窗测试（可未保存），返回用户数据库列表
- `POST /api/connections/{id}/test` — 已保存连接的连通测试（兼容）
- `POST /api/backups/run/{id}` — 立即备份所选/全部用户库（运维可用）
- `GET /api/backups` — 备份历史（可筛库、状态）
- `GET /api/backups/{id}/download` — 下载
- `DELETE /api/backups/{id}` — 删除记录及本地文件（管理员）
- `GET /api/dashboard` — 概览统计与最近备份
- `DELETE /api/dashboard/logs` — 清空概览备份日志（不删数据库服务器上的 .bak，运行中的任务保留）
- `GET/POST /api/schedules` — 定时任务
- `PUT/DELETE /api/schedules/{id}`
- `POST /api/schedules/{id}/pause`、`/resume`
- `GET/POST /api/remote-targets` — 群晖远程备份配置
- `PUT/DELETE /api/remote-targets/{id}`
- `POST /api/remote-targets/probe` — 测试群晖账号
- `GET/PUT /api/notify` — 飞书配置（管理员）
- `GET/POST /api/users` — 用户（管理员）
- `PUT/DELETE /api/users/{id}`

## 项目结构

```
sql_backup/
  app/                 FastAPI 后端
    routers/           登录、连接、备份、任务、用户、通知
    services/          备份执行、SSH、调度、飞书
  frontend/            Vue3 + Element Plus
  data/                唯一持久化目录：SQLite、密钥、临时 bak
  Dockerfile
  docker-compose.yml
  requirements.txt
```

系统配置库：`data/sql_backup.db`（已加入 `.gitignore`）。容器化时把整个 `data/` 映射出去即可。

## 容器化与数据持久化

**要持久化的全部放在同一个目录**，不要拆开。连接密码用 `secret.key` 加密后写入 SQLite，密钥和库必须一起挂载，否则容器一重建就解不开密码。

| 路径 | 内容 |
|------|------|
| `data/sql_backup.db` | 用户、连接、任务、飞书、群晖配置、备份记录 |
| `data/secret.key` | 加密密钥 / JWT |
| `data/backups/` | 平台偶发落到本机的临时 .bak（Windows 实例一般不落这里） |

本机默认就是项目下的 `data/`。容器里用环境变量改路径：`SQL_BACKUP_DATA_DIR=/data`。

### 怎么做成 Docker 镜像

工程里已经有 `Dockerfile` 和 `docker-compose.yml`。本机 **WSL Ubuntu 里已有 Docker Engine**，不必再装 Docker Desktop。在 WSL 终端执行：

```bash
cd /mnt/d/CURSOR/sql_backup
sudo docker compose up -d --build
```

（已把当前用户加入 `docker` 组，新开一个 WSL 窗口后一般不用再写 `sudo`。）

第一次会下载 Python/Node 基础镜像并编译前端，大约几分钟。成功后镜像名为 `sql-backup:latest`，页面仍是 http://127.0.0.1:8788 ，账号配置继续用现在的 `data/` 目录。

若 8788 被本机 `python -m app` 占用，先停掉再启动容器。

常用命令（在 WSL 项目目录下）：

```bash
sudo docker compose logs -f          # 看日志
sudo docker compose down             # 停容器（不删 data/）
sudo docker images sql-backup        # 确认镜像
```

如果要把镜像拷到另一台机器：

```bash
sudo docker save -o sql-backup.tar sql-backup:latest
# 到目标机后：
sudo docker load -i sql-backup.tar
sudo docker compose up -d
```

目标机同样需要把 `data/` 映射到 `/data`，否则是空库。

### 发布到 Docker Hub（开源仓库）别人怎么下载启动

公开镜像已发布：https://hub.docker.com/r/billyshang/sql-backup  

- `billyshang/sql-backup:v1.0.3` 固定版本（推荐群晖/生产，单架构 amd64，无 attestation）
- `billyshang/sql-backup:latest` 与当前 v1.0.3 相同
- `billyshang/sql-backup:v1.0.2` 历史版本
- `billyshang/sql-backup:v1.0.1` 历史版本
- `billyshang/sql-backup:v1.0.0` 历史版本（含 OCI index，旧群晖 Docker 可能拉到旧 digest）

**别人下载并启动**

```bash
docker pull billyshang/sql-backup:v1.0.3
mkdir -p data
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 \
  -e TZ=Asia/Shanghai \
  -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" \
  billyshang/sql-backup:v1.0.3
```

浏览器打开 `http://服务器IP:8788`。默认账号 `admin` / `admin@123`，登录后立刻改密。

用 Compose 也可以（仓库里的 `docker-compose.example.yml`）：

```bash
docker compose -f docker-compose.example.yml up -d
```

**群晖 / 1Panel / Portainer 这类面板**

- 镜像：`billyshang/sql-backup:v1.0.3`
- 端口：主机 `8788` → 容器 `8788`
- 存储：宿主机某个目录 → 容器 `/data`（必须映射，否则升级容器配置全丢）
- 环境变量：`TZ=Asia/Shanghai`，`SQL_BACKUP_DATA_DIR=/data`

本机重新构建后再发布（必须关 provenance，否则群晖旧 Docker 解析 OCI index 会异常）：

```bash
cd /mnt/d/CURSOR/sql_backup
sudo docker build --provenance=false --sbom=false --platform linux/amd64 \
  -t billyshang/sql-backup:v1.0.3 .
sudo docker tag billyshang/sql-backup:v1.0.3 billyshang/sql-backup:latest
sudo docker push billyshang/sql-backup:v1.0.3
sudo docker push billyshang/sql-backup:latest
```

没有公网仓库时，把 `sql-backup.tar` 拷过去 `docker load`，再按上面的 `docker run` 启动即可。

端口、默认管理员用环境变量即可，不必再单独做配置文件：

| 变量 | 默认 | 说明 |
|------|------|------|
| `SQL_BACKUP_DATA_DIR` | 项目下 `data/` | 持久化目录 |
| `SQL_BACKUP_HOST` | `0.0.0.0` | 监听地址 |
| `SQL_BACKUP_PORT` | `8788` | 监听端口 |
| `SQL_BACKUP_ADMIN_USER` | `admin` | 仅库为空时创建 |
| `SQL_BACKUP_ADMIN_PASS` | `admin@123` | 仅库为空时创建 |

## 可能改进

- python-tds 同一连接只能一个游标：先建目录再执行 BACKUP，避免 Cursor is closed
- 群晖旧 Docker 不兼容 OCI index/attestation；发布用 `--provenance=false --platform linux/amd64`，当前 `v1.0.3`
- 群晖容器用 python-tds 时，建备份目录改为逐级 xp_create_subdir（避免「参数无效」导致路径不存在）
- 数据目录可通过 `SQL_BACKUP_DATA_DIR` 指定，容器将整个 `data/` 映射到 `/data` 做持久化
- 顶栏用户名在前、角色在后；下拉可改密和打开使用文档；退出在左侧栏左下角
- 登录页改为白底居中卡片 + 三项能力说明，参考简洁后台登录风格
- 群晖路径为 `远程目录/连接名/库名/日期/文件名.bak`，便于多实例分开存放
- Windows 下控制台日志统一 UTF-8，避免中文显示成乱码
- 群晖上传已改为 File Station：登录 POST、SID 放在 URL、带 SynoToken；不再回退到未开放的 22 端口
- 备份进度百分比与实时日志推送（当前执行为同步请求，大库备份时页面会等待）
- 备份校验（RESTORE VERIFYONLY）
- 对象存储（OSS/S3）归档
- 双因素登录与审计日志导出
- Windows 实例的 `.bak` 只在数据库服务器上，平台网页「下载」通常不可用
