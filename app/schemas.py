"""Pydantic 入参/出参。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class PasswordBody(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=6, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: Literal["admin", "operator"] = "operator"


class UserPasswordIn(BaseModel):
    password: str = Field(..., min_length=6, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConnectionIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    db_type: str = Field(default="sqlserver", max_length=32)
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(default="", max_length=8000)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=256)
    connect_mode: Literal["direct", "ssh"] = "direct"
    backup_dir: str = Field(default="", max_length=512)
    ssh_proxy_id: int | None = 0
    ssh_host: str = Field(default="", max_length=256)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="", max_length=128)
    ssh_password: str = Field(default="", max_length=256)
    ssh_key: str = Field(default="", max_length=16000)
    remote_enabled: bool = False
    remote_target_id: int | None = 0


class ConnectionOut(BaseModel):
    id: int
    name: str
    db_type: str
    host: str
    port: int
    database: str
    username: str
    has_password: bool = False
    connect_mode: str
    backup_dir: str
    ssh_proxy_id: int = 0
    ssh_proxy_name: str = ""
    ssh_host: str
    ssh_port: int
    ssh_user: str
    has_ssh_password: bool = False
    has_ssh_key: bool = False
    remote_enabled: bool = False
    remote_target_id: int = 0
    remote_target_name: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ConnectionProbeIn(BaseModel):
    """弹窗内测试：可未保存。编辑时传 id，密码留空则用已存凭据。"""

    id: int | None = None
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=1433, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=256)
    connect_mode: Literal["direct", "ssh"] = "direct"
    ssh_proxy_id: int | None = 0
    ssh_host: str = Field(default="", max_length=256)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="", max_length=128)
    ssh_password: str = Field(default="", max_length=256)
    ssh_key: str = Field(default="", max_length=16000)


class ScheduleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    connection_id: int
    schedule_type: Literal["daily", "weekly", "once"] = "daily"
    run_time: str = Field(default="02:00", max_length=8)
    weekday: int = Field(default=0, ge=0, le=6)
    once_at: datetime | None = None
    backup_type: Literal["full", "diff", "log"] = "full"
    retain_days: int = Field(default=7, ge=1, le=3650)
    compress: bool = True
    delete_old: bool = True
    enabled: bool = True


class ScheduleOut(BaseModel):
    id: int
    name: str
    connection_id: int
    connection_name: str = ""
    database: str = ""
    schedule_type: str
    run_time: str
    weekday: int
    once_at: datetime | None = None
    backup_type: str
    retain_days: int
    compress: bool
    delete_old: bool
    enabled: bool
    last_status: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str = ""
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class BackupRunIn(BaseModel):
    backup_type: Literal["full", "diff", "log"] = "full"
    compress: bool = True
    retain_days: int = Field(default=7, ge=1, le=3650)
    delete_old: bool = True


class RestoreIn(BaseModel):
    connection_id: int
    backup_id: int | None = None
    file_path: str = Field(default="", max_length=4000)
    target_database: str = Field(..., min_length=1, max_length=128)
    replace: bool = False
    recovery: bool = True


class BackupOut(BaseModel):
    id: int
    connection_id: int
    connection_name: str = ""
    database: str = ""
    schedule_id: int | None = None
    backup_type: str
    status: str
    trigger: str
    file_path: str
    local_path: str
    file_size: int
    error_message: str = ""
    remote_path: str = ""
    remote_status: str = ""
    remote_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    downloadable: bool = False

    model_config = {"from_attributes": True}


class NotifyIn(BaseModel):
    feishu_webhook: str = Field(default="", max_length=1024)
    wecom_webhook: str = Field(default="", max_length=1024)
    dingtalk_webhook: str = Field(default="", max_length=1024)
    notify_channel: str = Field(default="feishu", max_length=64)
    enabled: bool = False
    notify_on_success: bool = True
    notify_on_fail: bool = True


class NotifyOut(BaseModel):
    feishu_webhook: str = ""
    wecom_webhook: str = ""
    dingtalk_webhook: str = ""
    notify_channel: str = "feishu"
    enabled: bool = False
    notify_on_success: bool = True
    notify_on_fail: bool = True

    model_config = {"from_attributes": True}


class RemoteTargetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=5001, ge=1, le=65535)
    https: bool = True
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=256)
    remote_dir: str = Field(default="/sql_backup", max_length=512)


class SshProxyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    host: str = Field(..., min_length=1, max_length=256)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(default="", max_length=256)
    key: str = Field(default="", max_length=16000)


class SshProxyOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    has_password: bool = False
    has_key: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class RemoteTargetOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    https: bool
    username: str
    has_password: bool = False
    remote_dir: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
