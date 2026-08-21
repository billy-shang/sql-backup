<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>备份文件与历史</h2>
        <div class="muted">路径是数据库服务器上的路径，在「备份目录\库名\日期\」子目录中，不是 D:\TEST 根目录。</div>
      </div>
        <div class="head-actions">
          <el-select v-model="filters.connection_id" clearable placeholder="全部连接" style="width:180px" @change="load">
            <el-option v-for="c in conns" :key="c.id" :label="connOptionLabel(c)" :value="c.id" />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="全部状态" style="width:120px" @change="load">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
            <el-option label="运行中" value="running" />
          </el-select>
          <el-input v-model="filters.q" placeholder="搜索库名/路径" clearable style="width:160px" @keyup.enter="load" @clear="load" />
          <el-button @click="load">刷新</el-button>
        </div>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无备份记录">
      <el-table-column prop="connection_name" label="连接" show-overflow-tooltip />
      <el-table-column prop="database" label="库名" width="100" show-overflow-tooltip />
      <el-table-column label="时间" width="136">
        <template #default="{ row }">{{ fmtTimeShort(row.started_at) }}</template>
      </el-table-column>
      <el-table-column label="类型" width="56">
        <template #default="{ row }">{{ typeMap[row.backup_type] || row.backup_type }}</template>
      </el-table-column>
      <el-table-column label="大小" width="72">
        <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="64">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small" :title="row.error_message || ''">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="路径" show-overflow-tooltip>
        <template #default="{ row }">
          <span :title="row.file_path">{{ fileBaseName(row.file_path) || "—" }}</span>
        </template>
      </el-table-column>
      <el-table-column label="群晖" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.remote_status === 'success'" :title="row.remote_path">{{ remoteFileName(row) }}</span>
          <span v-else-if="row.remote_status === 'failed'" style="color:#dc2626" :title="row.remote_error">{{ row.remote_error || "上传失败" }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" :width="admin ? 112 : 64">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text type="primary" :disabled="!row.downloadable" @click="download(row)">下载</el-button>
            <el-button v-if="admin" text type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { connOptionLabel, fileBaseName, fmtSize, fmtTimeShort, isAdmin, statusText, statusType, typeMap } from "../format";

const admin = isAdmin();
const loading = ref(false);
const items = ref([]);
const conns = ref([]);
const filters = reactive({ connection_id: null, status: "", q: "" });

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get("/backups", { params: filters });
    items.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}

function remoteFileName(row) {
  return fileBaseName(row.remote_path) || "已上传";
}

async function download(row) {
  try {
    const res = await http.get(`/backups/${row.id}/download`, { responseType: "blob" });
    const ctype = String(res.headers["content-type"] || "");
    if (ctype.includes("application/json")) {
      const text = await res.data.text();
      const j = JSON.parse(text);
      ElMessage.error(j.detail || "下载失败");
      return;
    }
    const name = (row.local_path || row.file_path || "backup.bak").split(/[/\\]/).pop();
    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function remove(row) {
  await ElMessageBox.confirm("删除该备份记录及本地文件？", "删除", { type: "warning" });
  try {
    await http.delete(`/backups/${row.id}`);
    ElMessage.success("已删除");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(async () => {
  try {
    const { data } = await http.get("/connections");
    conns.value = data.items || [];
  } catch {
    /* ignore */
  }
  load();
});
</script>
