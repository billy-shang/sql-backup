<template>
  <div>
    <div class="stat-grid">
      <div class="stat-item clickable" title="查看连接" @click="go('/connections')">
        <div class="n">{{ stats.connections }}</div>
        <div class="l">数据库连接</div>
      </div>
      <div class="stat-item clickable" title="查看定时任务" @click="go('/schedules')">
        <div class="n">{{ stats.schedules }}</div>
        <div class="l">定时任务</div>
      </div>
      <div class="stat-item clickable" title="查看成功记录" @click="goBackups({ status: 'success' })">
        <div class="n">{{ stats.success }}</div>
        <div class="l">备份成功</div>
      </div>
      <div class="stat-item clickable" title="查看失败记录" @click="goBackups({ status: 'failed' })">
        <div class="n" style="color:#dc2626">{{ stats.failed }}</div>
        <div class="l">备份失败</div>
      </div>
      <div class="stat-item clickable" title="查看运行中记录" @click="goBackups({ status: 'running' })">
        <div class="n" style="color:#d97706">{{ stats.running }}</div>
        <div class="l">运行中</div>
      </div>
    </div>
    <div class="page-card">
      <div class="page-head">
        <div>
          <h2>最近备份</h2>
          <div class="muted">点一行可跳到备份文件。失败记录可看原因。</div>
        </div>
        <div class="head-actions">
          <el-button v-if="admin" type="danger" plain @click="clearLogs">清空历史</el-button>
          <el-button @click="load">刷新</el-button>
        </div>
      </div>
      <el-table :data="recent" stripe class="fit-table recent-table" table-layout="fixed" empty-text="暂无备份记录" @row-click="openRecent">
        <el-table-column prop="name" label="连接" :min-width="narrow ? 110 : 140" show-overflow-tooltip />
        <el-table-column prop="database" label="数据库" :min-width="narrow ? 100 : 120" show-overflow-tooltip />
        <el-table-column prop="backup_type" label="类型" width="72" min-width="72">
          <template #default="{ row }">{{ typeMap[row.backup_type] || row.backup_type }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" min-width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" class="click-tag" :title="row.error_message || ''">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="!narrow" prop="file_size" label="大小" class-name="size-col" width="120" min-width="120">
          <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
        </el-table-column>
        <el-table-column prop="started_at" label="时间" width="168" min-width="168">
          <template #default="{ row }">{{ fmtTimeShort(row.started_at) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { fmtSize, fmtTimeShort, isAdmin, statusText, statusType, typeMap } from "../format";
import { useBreakpoints } from "../useBreakpoints";

const admin = isAdmin();
const { narrow } = useBreakpoints();
const router = useRouter();
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

function go(path) {
  router.push(path);
}

function goBackups(query) {
  router.push({ path: "/backups", query: query || {} });
}

async function openRecent(row) {
  if (row.status === "failed" && row.error_message) {
    try {
      await ElMessageBox.alert(row.error_message, "失败原因", { confirmButtonText: "查看记录" });
    } catch {
      return;
    }
  }
  goBackups(row.connection_id ? { connection_id: String(row.connection_id) } : {});
}

async function clearLogs() {
  try {
    await ElMessageBox.confirm(
      "将清空全部备份历史记录，成功/失败次数也会归零。数据库服务器上的 .bak 不会删除。正在运行的任务会保留。",
      "清空历史",
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

<style scoped>
.recent-table { cursor: pointer; }
</style>
