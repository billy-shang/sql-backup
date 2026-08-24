<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>备份文件与历史</h2>
        <div class="muted">路径是数据库服务器上的路径，在「备份目录\库名\日期\」子目录中。管理员可从成功记录或目录里的 .bak 恢复到指定库名。</div>
      </div>
        <div class="head-actions">
          <el-select v-model="filters.connection_id" clearable placeholder="全部连接" style="width:180px" @change="filterChange">
            <el-option v-for="c in conns" :key="c.id" :label="connOptionLabel(c)" :value="c.id" />
          </el-select>
          <el-select v-model="filters.status" clearable placeholder="全部状态" style="width:120px" @change="filterChange">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
            <el-option label="运行中" value="running" />
          </el-select>
          <el-input v-model="filters.q" placeholder="搜索库名/路径" clearable style="width:160px" @keyup.enter="filterChange" @clear="filterChange" />
          <el-button @click="openCatalog">浏览目录</el-button>
          <el-button @click="load">刷新</el-button>
        </div>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无备份记录">
      <el-table-column prop="connection_name" label="连接" show-overflow-tooltip />
      <el-table-column prop="database" label="库名" width="120" show-overflow-tooltip />
      <el-table-column label="时间" width="152">
        <template #default="{ row }">{{ fmtTimeShort(row.started_at) }}</template>
      </el-table-column>
      <el-table-column label="类型" width="64">
        <template #default="{ row }">{{ typeMap[row.backup_type] || row.backup_type }}</template>
      </el-table-column>
      <el-table-column label="大小" width="96">
        <template #default="{ row }">{{ fmtSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="80">
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
      <el-table-column label="操作" class-name="ops-col" :width="admin ? 210 : 80">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text type="primary" :disabled="!row.downloadable" @click="download(row)">下载</el-button>
            <el-button
              v-if="admin"
              text
              type="success"
              :disabled="!canRestore(row)"
              @click="openRestore(row)"
            >恢复</el-button>
            <el-button v-if="admin" text type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>
    <div v-if="restoreState" class="restore-prog">
      <div class="prog-bar" :class="restoreState.status" :title="restoreState.message || ''">
        <div class="prog-fill" :style="{ width: restoreState.percent + '%' }"></div>
        <span class="prog-text">{{ restoreProgressText }}</span>
      </div>
    </div>
    <div class="pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>

    <el-dialog v-model="catalogDlg" title="服务器备份目录" width="640px" :close-on-click-modal="false">
      <el-select v-model="catalogCid" placeholder="选择连接" style="width:100%;margin-bottom:12px" @change="loadCatalog">
        <el-option v-for="c in conns" :key="c.id" :label="connOptionLabel(c)" :value="c.id" />
      </el-select>
      <div v-if="catalogRoot" class="muted" style="margin-bottom:8px">{{ catalogRoot }}{{ admin ? "。点 .bak 文件可打开恢复向导" : "" }}</div>
      <el-tree
        v-loading="catalogLoading"
        :data="catalogTree"
        :props="{ label: 'label', children: 'children' }"
        default-expand-all
        empty-text="该目录下没有日期备份"
        @node-click="onCatalogNode"
      />
    </el-dialog>

    <el-dialog v-model="restoreDlg" title="恢复向导" width="560px" :close-on-click-modal="false">
      <el-form :model="restoreForm" label-width="110px">
        <el-form-item label="备份文件">
          <div class="path-box" :title="restoreForm.file_path">{{ restoreForm.file_path || "—" }}</div>
        </el-form-item>
        <el-form-item label="目标连接">
          <el-select v-model="restoreForm.connection_id" style="width:100%" @change="loadRestorePreview">
            <el-option v-for="c in conns" :key="c.id" :label="connOptionLabel(c)" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标库名">
          <el-input v-model="restoreForm.target_database" placeholder="恢复后的数据库名，可与原库不同" />
        </el-form-item>
        <el-form-item label="覆盖已有库">
          <el-switch v-model="restoreForm.replace" />
          <span class="muted" style="margin-left:8px">勾选后会踢掉该库现有连接并覆盖数据</span>
        </el-form-item>
        <el-form-item label="恢复后联机">
          <el-switch v-model="restoreForm.recovery" />
          <span class="muted" style="margin-left:8px">关闭则 NORECOVERY，库保持还原中</span>
        </el-form-item>
      </el-form>
      <div v-if="previewLoading" class="muted">正在读取备份头…</div>
      <div v-else-if="restorePreview" class="preview-box">
        <div>来源库：{{ restorePreview.source_database || "—" }}　类型：{{ restorePreview.backup_type_label || "—" }}　备份时间：{{ restorePreview.backup_finish || "—" }}</div>
        <div v-if="restorePreview.reason" class="warn">{{ restorePreview.reason }}</div>
        <div class="muted" style="margin-top:6px">.bak 必须在目标 SQL Server 本机可见。换到另一台机器上的连接通常会失败。</div>
      </div>
      <template #footer>
        <el-button @click="restoreDlg = false">取消</el-button>
        <el-button type="primary" :loading="restoreSubmitting" :disabled="!canSubmitRestore" @click="doRestore">开始恢复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { connOptionLabel, fileBaseName, fmtSize, fmtTimeShort, isAdmin, statusText, statusType, typeMap } from "../format";

const admin = isAdmin();
const loading = ref(false);
const items = ref([]);
const conns = ref([]);
const filters = reactive({ connection_id: null, status: "", q: "" });
const page = ref(1);
const pageSize = ref(20);
const total = ref(0);
const catalogDlg = ref(false);
const catalogCid = ref(null);
const catalogRoot = ref("");
const catalogTree = ref([]);
const catalogLoading = ref(false);
const restoreDlg = ref(false);
const restoreSubmitting = ref(false);
const previewLoading = ref(false);
const restorePreview = ref(null);
const restoreState = ref(null);
const restoreForm = reactive({
  connection_id: null,
  backup_id: null,
  file_path: "",
  target_database: "",
  replace: false,
  recovery: true,
});
let restoreTimer = 0;

const canSubmitRestore = computed(() => {
  const name = (restoreForm.target_database || "").trim();
  if (!restoreForm.connection_id || !restoreForm.file_path || !name) return false;
  if (previewLoading.value) return false;
  if (restorePreview.value && restorePreview.value.can_restore === false) return false;
  return true;
});

const restoreProgressText = computed(() => {
  const state = restoreState.value;
  if (!state) return "";
  if (state.status === "success") return state.message || "恢复完成";
  if (state.status === "failed") return state.message || "恢复失败";
  return `${state.message || "恢复中"} ${state.percent || 0}%`;
});

function canRestore(row) {
  if (!row || row.status !== "success" || !row.file_path) return false;
  const st = restoreState.value;
  if (st && st.status === "running" && st.connection_id === row.connection_id) return false;
  return true;
}

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get("/backups", {
      params: {
        connection_id: filters.connection_id || undefined,
        status: filters.status || undefined,
        q: filters.q || undefined,
        page: page.value,
        page_size: pageSize.value,
      },
    });
    items.value = data.items || [];
    total.value = Number(data.total || 0);
    page.value = Number(data.page || page.value);
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}

function onSizeChange() {
  page.value = 1;
  load();
}

function filterChange() {
  page.value = 1;
  load();
}

function openCatalog() {
  catalogCid.value = filters.connection_id || (conns.value[0] && conns.value[0].id) || null;
  catalogDlg.value = true;
  if (catalogCid.value) loadCatalog();
}

async function loadCatalog() {
  if (!catalogCid.value) return;
  catalogLoading.value = true;
  catalogTree.value = [];
  catalogRoot.value = "";
  try {
    const { data } = await http.get("/backups/catalog", { params: { connection_id: catalogCid.value } });
    catalogRoot.value = data.root || "";
    catalogTree.value = (data.items || []).map((db) => ({
      label: db.database,
      children: (db.days || []).map((day) => ({
        label: (day.name || "（未分日期）") + (day.files && day.files.length ? `（${day.files.length}）` : ""),
        children: (day.files || []).map((f) => ({ label: f.name, path: f.path })),
      })),
    }));
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    catalogLoading.value = false;
  }
}

function onCatalogNode(data) {
  if (!admin || !data || !data.path) return;
  console.log("[restore] 从目录选择", data.path);
  openRestore({
    id: null,
    connection_id: catalogCid.value,
    file_path: data.path,
    database: "",
    status: "success",
  });
}

function openRestore(row) {
  restoreForm.connection_id = row.connection_id;
  restoreForm.backup_id = row.id || null;
  restoreForm.file_path = row.file_path || "";
  restoreForm.target_database = row.database || "";
  restoreForm.replace = false;
  restoreForm.recovery = true;
  restorePreview.value = null;
  catalogDlg.value = false;
  restoreDlg.value = true;
  loadRestorePreview();
}

async function loadRestorePreview() {
  if (!restoreForm.connection_id || !restoreForm.file_path) return;
  previewLoading.value = true;
  restorePreview.value = null;
  try {
    const { data } = await http.get("/backups/restore/preview", {
      params: {
        connection_id: restoreForm.connection_id,
        backup_id: restoreForm.backup_id || undefined,
        file_path: restoreForm.file_path,
      },
    });
    restorePreview.value = data;
    if (!(restoreForm.target_database || "").trim() && data.source_database) {
      restoreForm.target_database = data.source_database;
    }
    console.log("[restore] 预览", data.source_database, data.backup_type);
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    previewLoading.value = false;
  }
}

async function doRestore() {
  const target = (restoreForm.target_database || "").trim();
  if (!canSubmitRestore.value) return;
  if (restoreForm.replace) {
    await ElMessageBox.confirm(`确定用该备份覆盖数据库 ${target}？现有数据会丢掉。`, "覆盖确认", { type: "warning" });
  } else {
    await ElMessageBox.confirm(`确定恢复到数据库 ${target}？`, "恢复确认", { type: "warning" });
  }
  restoreSubmitting.value = true;
  try {
    console.log("[restore] 提交", restoreForm.connection_id, target);
    await http.post("/backups/restore", {
      connection_id: restoreForm.connection_id,
      backup_id: restoreForm.backup_id || undefined,
      file_path: restoreForm.file_path,
      target_database: target,
      replace: restoreForm.replace,
      recovery: restoreForm.recovery,
    });
    restoreDlg.value = false;
    startRestoreProgress(restoreForm.connection_id, target);
    ElMessage.success("已开始恢复，进度见页面下方");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    restoreSubmitting.value = false;
  }
}

function startRestoreProgress(cid, target) {
  restoreState.value = {
    connection_id: cid,
    percent: 1,
    status: "running",
    message: `正在恢复到 ${target}`,
  };
  pollRestore(cid);
}

function applyRestoreJob(item, toast) {
  if (!item) return;
  const prev = restoreState.value && restoreState.value.status;
  restoreState.value = {
    connection_id: item.connection_id,
    percent: Number(item.percent) || 0,
    status: item.status || "running",
    message: item.message || "",
    kind: item.kind || "restore",
  };
  if (item.status === "success") {
    stopRestoreTimer();
    if (toast && prev === "running") ElMessage.success(item.message || "恢复完成");
    window.setTimeout(() => {
      if (restoreState.value && restoreState.value.status === "success") restoreState.value = null;
    }, 4000);
  } else if (item.status === "failed") {
    stopRestoreTimer();
    if (toast && prev === "running") ElMessage.error(item.message || "恢复失败");
    window.setTimeout(() => {
      if (restoreState.value && restoreState.value.status === "failed") restoreState.value = null;
    }, 8000);
  }
}

function pollRestore(cid) {
  stopRestoreTimer();
  const tick = async () => {
    try {
      const { data } = await http.get(`/backups/progress/${cid}`);
      if (!data.item) return;
      applyRestoreJob(data.item, true);
    } catch {
      /* 恢复仍在跑时瞬时错误下一轮继续问 */
    }
  };
  tick();
  restoreTimer = window.setInterval(tick, 800);
}

function stopRestoreTimer() {
  if (restoreTimer) {
    window.clearInterval(restoreTimer);
    restoreTimer = 0;
  }
}

async function restoreProgressOnLoad() {
  try {
    const { data } = await http.get("/backups/progress");
    for (const item of data.items || []) {
      if (item.kind === "restore" || (item.status === "running" && String(item.message || "").includes("恢复"))) {
        applyRestoreJob(item, false);
        if (item.status === "running") pollRestore(item.connection_id);
      }
    }
  } catch {
    /* ignore */
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
  if (admin) restoreProgressOnLoad();
});

onBeforeUnmount(() => {
  stopRestoreTimer();
});
</script>

<style scoped>
.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 0 0;
}
.path-box {
  word-break: break-all;
  line-height: 1.4;
  color: #334155;
}
.preview-box {
  background: #f8fafc;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
}
.preview-box .warn {
  color: #b45309;
  margin-top: 6px;
}
.restore-prog {
  margin-top: 12px;
}
.prog-bar {
  position: relative;
  height: 22px;
  border-radius: 11px;
  background: #eef8f1;
  overflow: hidden;
}
.prog-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  background: linear-gradient(90deg, #d8f6e2, #a8e6be);
  border-radius: 11px;
  transition: width 0.45s ease;
}
.prog-text {
  position: relative;
  z-index: 1;
  display: block;
  height: 22px;
  line-height: 22px;
  text-align: center;
  font-size: 12px;
  font-weight: 650;
  color: #2f6b45;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 0 8px;
}
.prog-bar.failed .prog-fill {
  background: linear-gradient(90deg, #fde2e2, #f5b4b4);
}
.prog-bar.failed .prog-text { color: #9b2c2c; }
.prog-bar.success .prog-fill {
  background: linear-gradient(90deg, #c8efd4, #8ed9a8);
}
</style>
