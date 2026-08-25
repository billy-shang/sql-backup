# SQL Backup

SQL Server 备份管理平台。平台可部署在任意机器；`.bak` 写在 **SQL Server 所在服务器**，不是本平台。

- 源码：https://github.com/billy-shang/sql-backup
- 镜像：https://hub.docker.com/r/billyshang/sql-backup （当前 `v1.0.48`）

## 功能

- 配置中心：远程备份（群晖）、SSH 代理、通知；连接里下拉选用
- 多实例连接（直连 / SSH），完整 / 差异 / 日志备份
- 定时任务、保留策略、群晖 File Station 远程归档
- 恢复向导：从完整备份恢复到指定库名（可覆盖）
- WebHook 通知：飞书 / 企业微信 / 钉钉（可单选或同时启用）
- 管理员 / 普通运维

备份路径：`{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak`  

## 界面

### 概览

![概览](IMAGE/1概览.png)

### 数据库连接

![数据库连接](IMAGE/2数据库连接.png)

### 备份文件

![备份文件](IMAGE/3备份文件.png)

### 定时任务

![定时任务](IMAGE/4定时任务.png)

### 配置中心 · 远程备份（群晖）

![配置中心-群晖](IMAGE/5-1配置中心-群晖.png)

### 配置中心 · SSH 代理

![配置中心-SSH代理](IMAGE/5-2配置中心-SSH代理.png)

### 配置中心 · 通知

![配置中心-通知](IMAGE/5-3配置中心-通知.png)

### 用户管理

![用户管理](IMAGE/6用户管理.png)

## 运行

```bash
pip install -r requirements.txt
python -m app
```

打开 http://127.0.0.1:8788 ，默认 `admin` / `admin@123`

## Docker 部署

```bash
docker pull billyshang/sql-backup:v1.0.48
docker run -d --name sql-backup --restart unless-stopped \
  -p 8788:8788 -e TZ=Asia/Shanghai -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" billyshang/sql-backup:v1.0.48
```


