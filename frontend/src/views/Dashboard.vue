<template>
  <div>
    <div class="stat-grid">
      <div class="stat-item"><div class="n">{{ stats.connections }}</div><div class="l">数据库连接</div></div>
      <div class="stat-item"><div class="n">{{ stats.schedules }}</div><div class="l">定时任务</div></div>
      <div class="stat-item"><div class="n">{{ stats.success }}</div><div class="l">备份成功</div></div>
      <div class="stat-item"><div class="n" style="color:#dc2626">{{ stats.failed }}</div><div class="l">备份失败</div></div>
      <div class="stat-item"><div class="n" style="color:#d97706">{{ stats.running }}</div><div class="l">运行中</div></div>
    </div>
    <div class="page-card">
      <div class="page-head">
        <h2>最近备份</h2>
        <div style="display:flex;gap:8px">
          <el-button type="danger" plain @click="clearLogs">清空日志</el-button>
          <el-button @click="load">刷新</el-button>
        </div>
      </div>
      <el-table :data="recent" stripe>
        <el-table-column prop="name" label="连接名称" min-width="140" />
        <el-table-column prop="database" label="数据库" min-width="100" />
        <el-table-column prop="backup_type" label="类型" width="100">
          <template #default="{ row }">{{ typeMap[row.backup_type] || row.backup_type }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="file_size" label="大小" width="110">
          <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="started_at" label="时间" min-width="170">
          <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { fmtSize, fmtTime, statusText, statusType, typeMap } from "../format";

const stats = reactive({ connections: 0, schedules: 0, success: 0, failed: 0, running: 0 });
const recent = ref([]);

async function load() {
  try {
    const { data } = await http.get("/dashboard");
    Object.assign(stats, data.stats || {});
    recent.value = data.recent || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function clearLogs() {
  try {
    await ElMessageBox.confirm(
      "将清空概览中的全部备份记录，成功/失败次数也会归零。数据库服务器上的 .bak 文件不会删除。正在运行的任务会保留。",
      "清空日志",
      { type: "warning", confirmButtonText: "清空", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  try {
    const { data } = await http.delete("/dashboard/logs");
    ElMessage.success(data.message || "已清空");
    await load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(load);
</script>
