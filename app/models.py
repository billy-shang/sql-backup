"""系统表：用户、数据库连接、定时任务、备份记录、通知配置。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")  # admin | operator
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DbConnection(Base):
    __tablename__ = "db_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    db_type: Mapped[str] = mapped_column(String(32), nullable=False, default="sqlserver")
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=1433)
    # 空=全部用户库；多个库用逗号分隔
    database: Mapped[str] = mapped_column(Text, nullable=False, default="")
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    connect_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="direct")  # direct | ssh
    backup_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="/backup/sqlserver")
    remote_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    remote_target_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ssh_host: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ssh_password_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ssh_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    schedules: Mapped[list[Schedule]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )
    backups: Mapped[list[BackupRecord]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    connection_id: Mapped[int] = mapped_column(ForeignKey("db_connections.id", ondelete="CASCADE"))
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False, default="daily")  # daily|weekly|once
    run_time: Mapped[str] = mapped_column(String(8), nullable=False, default="02:00")
    weekday: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0=周一 ... 6=周日
    once_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backup_type: Mapped[str] = mapped_column(String(16), nullable=False, default="full")  # full|diff|log
    retain_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    compress: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    delete_old: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # running|success|failed|paused
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    connection: Mapped[DbConnection] = relationship(back_populates="schedules")


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("db_connections.id", ondelete="CASCADE"))
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True)
    dbname: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    backup_type: Mapped[str] = mapped_column(String(16), nullable=False, default="full")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")  # manual|schedule
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    local_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remote_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    remote_status: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    remote_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    connection: Mapped[DbConnection] = relationship(back_populates="backups")


class NotifyConfig(Base):
    __tablename__ = "notify_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feishu_webhook: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    wecom_webhook: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    notify_channel: Mapped[str] = mapped_column(String(16), nullable=False, default="feishu")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_on_success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_on_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=func.now())


class RemoteTarget(Base):
    """群晖等远程备份目标：地址、账号、密码、远程目录。"""

    __tablename__ = "remote_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    host: Mapped[str] = mapped_column(String(256), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=5001)
    https: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_enc: Mapped[str] = mapped_column(Text, nullable=False, default="")
    remote_dir: Mapped[str] = mapped_column(String(512), nullable=False, default="/sql_backup")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
