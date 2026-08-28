# SQL Backup

<p align="center">
  <strong>一个轻量、开源、自托管的 SQL Server 备份与恢复管理平台</strong>
</p>

<p align="center">
  支持多实例管理、定时备份、备份恢复、SSH 代理、NAS / 群晖归档及 WebHook 通知
</p>

<p align="center">
  <a href="https://github.com/billy-shang/sql-backup/stargazers">
    <img src="https://img.shields.io/github/stars/billy-shang/sql-backup?style=flat-square" alt="GitHub Stars">
  </a>
  <a href="https://github.com/billy-shang/sql-backup">
    <img src="https://img.shields.io/github/license/billy-shang/sql-backup?style=flat-square" alt="License">
  </a>
  <a href="https://hub.docker.com/r/billyshang/sql-backup">
    <img src="https://img.shields.io/docker/pulls/billyshang/sql-backup?style=flat-square" alt="Docker Pulls">
  </a>
  <img src="https://img.shields.io/badge/SQL%20Server-Backup-blue?style=flat-square" alt="SQL Server">
  <img src="https://img.shields.io/badge/Docker-Supported-blue?style=flat-square" alt="Docker">
</p>

<p align="center">
  <a href="https://github.com/billy-shang/sql-backup">GitHub</a>
  ·
  <a href="https://hub.docker.com/r/billyshang/sql-backup">Docker Hub</a>
</p>

---

## ✨ 项目简介

**SQL Backup** 是一个面向 DBA、运维工程师及中小型团队的 SQL Server Web 备份管理平台。

通过简单的 Web 界面即可统一管理多个 SQL Server 实例，实现数据库的 **完整备份、差异备份、日志备份、定时任务、备份恢复、保留策略以及 NAS / 群晖远程归档**。

无需在每台数据库服务器部署复杂的 Agent，一个 SQL Backup 实例即可集中管理多套 SQL Server。

> [!IMPORTANT]
> SQL Server 生成的 `.bak` 文件默认保存在 **SQL Server 所在服务器**，而不是 SQL Backup 平台服务器。
>
> 如配置远程归档，可进一步将备份文件自动上传至群晖 NAS。

---

## 🚀 核心功能

| 功能 | 说明 |
| --- | --- |
| 🗄️ 多实例管理 | 集中管理多个 SQL Server 实例 |
| 💾 多种备份模式 | 支持完整备份、差异备份、事务日志备份 |
| ⏰ 定时备份 | 按计划自动执行数据库备份 |
| ♻️ 保留策略 | 自动管理历史备份，减少磁盘空间占用 |
| 🔄 数据库恢复 | 通过恢复向导将完整备份恢复至指定数据库 |
| 🌐 SSH 代理 | 支持通过 SSH 代理访问远程数据库 |
| 📦 NAS 归档 | 支持群晖 File Station 自动归档备份 |
| 🔔 WebHook 通知 | 支持飞书、企业微信、钉钉，可同时启用 |
| 👥 用户管理 | 支持管理员 / 普通运维角色 |
| 🐳 Docker 部署 | 支持 Docker 快速部署及数据持久化 |

---

## 🖥️ 界面预览

<p align="center">
  <strong>登录</strong><br/>
  <img src="IMAGE/0登录.png" alt="SQL Backup 登录" />
</p>

<table>
  <tr>
    <td align="center" width="50%"><strong>系统概览</strong><br/><img src="IMAGE/1概览.png" alt="系统概览" /></td>
    <td align="center" width="50%"><strong>数据库连接</strong><br/><img src="IMAGE/2数据库连接.png" alt="数据库连接" /></td>
  </tr>
  <tr>
    <td align="center" width="50%"><strong>备份文件</strong><br/><img src="IMAGE/3备份文件.png" alt="备份文件" /></td>
    <td align="center" width="50%"><strong>定时任务</strong><br/><img src="IMAGE/4定时任务.png" alt="定时任务" /></td>
  </tr>
  <tr>
    <td align="center" width="50%"><strong>群晖备份</strong><br/><img src="IMAGE/5-1配置中心-群晖.png" alt="群晖备份" /></td>
    <td align="center" width="50%"><strong>SSH 代理</strong><br/><img src="IMAGE/5-2配置中心-SSH代理.png" alt="SSH代理" /></td>
  </tr>
  <tr>
    <td align="center" width="50%"><strong>通知配置</strong><br/><img src="IMAGE/5-3配置中心-通知.png" alt="通知配置" /></td>
    <td align="center" width="50%"><strong>用户管理</strong><br/><img src="IMAGE/6用户管理.png" alt="用户管理" /></td>
  </tr>
</table>

---

## ⚡ 快速开始

推荐使用 Docker 部署。

### Docker

```bash
docker run -d \
  --name sql-backup \
  --restart unless-stopped \
  -p 8788:8788 \
  -e TZ=Asia/Shanghai \
  -e SQL_BACKUP_DATA_DIR=/data \
  -v "$PWD/data:/data" \
  billyshang/sql-backup:v1.0.52
```

部署完成后访问：

```text
http://服务器IP:8788
```

默认管理员：

```text
用户名：admin
密码：admin@123
```

> [!WARNING]
> 首次登录后请立即修改默认管理员密码。

---

## 📂 备份目录

SQL Server 本地备份默认按照以下结构保存：

```text
{备份目录}
└── {数据库名}
    └── {YYYY-MM-DD}
        └── {数据库名}_{时间}_{备份类型}.bak
```

例如：

```text
D:\SQLBackup
└── ERP
    └── 2026-08-28
        └── ERP_020000_FULL.bak
```

这样可以按照 **数据库 → 日期 → 备份文件** 的方式快速定位历史备份。

---

## 🔄 备份流程

```text
                    ┌─────────────────────┐
                    │     SQL Backup      │
                    │      Web 管理       │
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴─────────────┐
                 │                           │
              直连 SQL                    SSH 代理
                 │                           │
                 └─────────────┬─────────────┘
                               ▼
                    ┌─────────────────────┐
                    │     SQL Server      │
                    └──────────┬──────────┘
                               │
                         SQL BACKUP
                               │
                               ▼
                    ┌─────────────────────┐
                    │   SQL Server 本地   │
                    │      .bak 文件      │
                    └──────────┬──────────┘
                               │
                         可选远程归档
                               │
                               ▼
                    ┌─────────────────────┐
                    │     Synology NAS    │
                    │    File Station     │
                    └─────────────────────┘
```

SQL Backup 负责 **调度和管理备份任务**。

实际 `.bak` 文件由 SQL Server 生成，因此备份目录必须是 **SQL Server 服务能够访问的目录**。

---

## 📦 群晖 NAS 远程归档

SQL Backup 支持将 SQL Server 生成的 `.bak` 文件自动归档至群晖 NAS。

Windows SQL Server 优先由 **SQL Server 所在服务器直接上传至群晖**，避免大型备份文件经过 SQL Backup 平台二次中转。

支持：

- Synology File Station API
- HTTP `5000`
- HTTPS `5001`
- PowerShell 2.0+
- curl / HttpWebRequest 流式上传
- 大文件备份归档

网络需要满足：

```text
SQL Server
     │
     │ HTTP / HTTPS
     ▼
Synology NAS
5000 / 5001
```

因此 SQL Server 所在服务器需要能够直接访问群晖 File Station。

---

## 🔔 通知

支持通过 WebHook 推送备份结果：

- 飞书
- 企业微信
- 钉钉

可以单独启用，也可以同时启用多个通知渠道。

适合将数据库备份结果直接发送到运维群，实现备份任务异常及时发现。

---

## 🛠️ 源码运行

### 1. 克隆项目

```bash
git clone https://github.com/billy-shang/sql-backup.git
cd sql-backup
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

```bash
python -m app
```

访问：

```text
http://127.0.0.1:8788
```

---

## 🐳 Docker 镜像

Docker Hub：

```text
billyshang/sql-backup
```

拉取当前版本：

```bash
docker pull billyshang/sql-backup:v1.0.52
```

查看镜像：

https://hub.docker.com/r/billyshang/sql-backup

---

## 🗺️ Roadmap

后续计划逐步完善：

- [ ] 更多 NAS / 对象存储支持
- [ ] S3 / MinIO 备份归档
- [ ] 备份文件完整性校验
- [ ] 自动恢复验证
- [ ] 备份任务统计与趋势分析
- [ ] 更完善的权限管理
- [ ] 多语言支持
- [ ] 更多数据库支持

欢迎通过 Issue 提交 Bug、功能建议或使用反馈。

---

## 🤝 参与贡献

如果你在使用过程中遇到问题，欢迎：

- 提交 Issue
- 提交 Pull Request
- 分享使用场景
- 完善文档
- 提出新的备份需求

项目地址：

https://github.com/billy-shang/sql-backup

---

## ⭐ 支持项目

如果 SQL Backup 对你的数据库备份或日常运维工作有所帮助，欢迎点一个 **Star ⭐**。

你的支持会让这个项目持续完善。

<p align="center">
  <strong>⭐ Star · 🐛 Issue · 🔀 Pull Request</strong>
</p>
