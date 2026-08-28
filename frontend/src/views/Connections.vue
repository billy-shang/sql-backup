<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>数据库连接</h2>
        <div class="muted">平台可部署在任意机器。备份文件写在 SQL Server 本机的「本地备份目录」，不会写到本平台。</div>
      </div>
      <div class="head-actions">
        <el-button v-if="admin" type="primary" @click="openEdit()">新增连接</el-button>
      </div>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无连接">
      <el-table-column prop="name" label="名称" :min-width="narrow ? 100 : 140" show-overflow-tooltip />
      <el-table-column v-if="!narrow" label="地址" width="168" min-width="168" show-overflow-tooltip>
        <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
      </el-table-column>
      <el-table-column v-if="!narrow" label="方式" width="72" min-width="72">
        <template #default="{ row }">{{ row.connect_mode === "ssh" ? "SSH" : "直连" }}</template>
      </el-table-column>
      <el-table-column label="数据库" class-name="db-col" :min-width="narrow ? 180 : 280">
        <template #default="{ row }">
          <div class="db-tags">
            <span
              v-for="name in dbTagItems(row)"
              :key="name"
              class="db-chip"
              :class="{ sys: isSystemDb(name) }"
              :title="name"
            >{{ name }}</span>
            <span v-if="!dbTagItems(row).length" class="db-chip all">全部用户库</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="进度" :width="narrow ? 120 : 160" :min-width="narrow ? 120 : 160">
        <template #default="{ row }">
          <div
            v-if="backupStates[row.id]"
            class="prog-bar"
            :class="backupStates[row.id].status"
            :title="backupStates[row.id].message || progressText(backupStates[row.id])"
          >
            <div class="prog-fill" :style="{ width: backupStates[row.id].percent + '%' }"></div>
            <span class="prog-text">{{ progressText(backupStates[row.id]) }}</span>
          </div>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" class-name="ops-col" align="left" :width="opsWidth" :min-width="opsWidth">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text type="success" :disabled="backupStates[row.id]?.status === 'running'" @click="openRun(row)">备份</el-button>
            <el-button v-if="!narrow" text @click="openBackups(row)">记录</el-button>
            <el-button v-if="!narrow && admin" text @click="openEdit(row)">编辑</el-button>
            <el-button v-if="admin" text type="danger" @click="remove(row)">删除</el-button>
            <el-dropdown v-if="narrow" trigger="click">
              <el-button text>更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="openBackups(row)">记录</el-dropdown-item>
                  <el-dropdown-item v-if="admin" @click="openEdit(row)">编辑</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="dlg"
      :title="form.id ? '编辑连接' : '新增连接'"
      width="720px"
      :close-on-click-modal="false"
    >
      <el-form :model="form" :label-width="narrow ? '96px' : '128px'">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="生产SQLServer" /></el-form-item>
        <el-form-item label="数据库类型">
          <el-select v-model="form.db_type" style="width:100%">
            <el-option label="SQL Server" value="sqlserver" />
          </el-select>
        </el-form-item>
        <el-form-item label="地址">
          <div class="pair-row">
            <el-input class="grow" v-model="form.host" placeholder="192.168.1.10" />
            <el-input-number class="port" v-model="form.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="数据库名">
          <div v-if="!dbOptions.length" class="muted">
            默认为全部用户数据库，可留空。点击「测试」后会列出全部库；系统库会标注，默认不勾选。
          </div>
          <div v-else class="db-box">
            <div class="db-toolbar">
              <el-button text type="primary" @click="selectAll">全选</el-button>
              <el-button text type="primary" @click="selectUserOnly">仅用户库</el-button>
              <el-button text @click="selectedDbs = []">全不选（仅用户库）</el-button>
              <span class="muted">已选 {{ selectedDbs.length }} / {{ dbOptions.length }}</span>
            </div>
            <el-checkbox-group v-model="selectedDbs" class="db-group">
              <el-checkbox v-for="db in dbOptions" :key="db.name" :label="db.name" :value="db.name">
                <span class="db-name" :title="db.name">{{ db.name }}</span>
                <el-tag v-if="db.is_system" size="small" type="warning" class="sys-tag">系统库</el-tag>
              </el-checkbox>
            </el-checkbox-group>
          </div>
        </el-form-item>
        <el-form-item label="用户名"><el-input v-model="form.username" placeholder="sa" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="form.password" type="password" show-password :placeholder="form.id ? '留空则使用已保存密码' : '必填'" />
        </el-form-item>
        <el-form-item label="连接方式">
          <el-radio-group v-model="form.connect_mode">
            <el-radio value="direct">直连</el-radio>
            <el-radio value="ssh">SSH 代理</el-radio>
          </el-radio-group>
          <div class="muted">直连要求平台能访问 SQL 端口；SSH 经配置中心的跳板再连。</div>
        </el-form-item>
        <el-form-item label="本地备份目录">
          <el-input v-model="form.backup_dir" placeholder="留空=SQL Server 默认 Backup 目录" />
          <div class="muted">
            填 SQL Server 本机路径，不是本平台路径。留空则用实例默认 Backup 目录。例如 D:\TEST 或 G:\sql_backup。文件在「目录\库名\日期\」子目录里，根目录通常看不到 .bak。
          </div>
        </el-form-item>
        <el-form-item label="是否远程备份">
          <el-switch v-model="form.remote_enabled" />
        </el-form-item>
        <el-form-item v-if="form.remote_enabled" label="远程备份">
          <div class="select-with-add">
            <el-select v-model="form.remote_target_id" placeholder="选择群晖配置" clearable>
              <el-option v-for="t in remotes" :key="t.id" :label="`${t.name}（${t.host}）`" :value="t.id" />
            </el-select>
            <el-button @click="openQuickRemote">新增</el-button>
          </div>
          <div class="muted">备份完成后把 .bak 上传到所选群晖。</div>
        </el-form-item>
        <el-form-item v-if="form.connect_mode === 'ssh'" label="SSH 代理">
          <div class="select-with-add">
            <el-select v-model="form.ssh_proxy_id" placeholder="选择 SSH 跳板" clearable>
              <el-option v-for="p in proxies" :key="p.id" :label="`${p.name}（${p.username}@${p.host}:${p.port}）`" :value="p.id" />
            </el-select>
            <el-button @click="openQuickSsh">新增</el-button>
          </div>
          <div class="muted">经所选跳板再连 SQL，不用在这里手填账号密码。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="success" :loading="testing" @click="probe">测试</el-button>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="quickRemoteDlg" title="新增群晖" width="520px" :close-on-click-modal="false" append-to-body>
      <el-form :model="quickRemote" :label-width="narrow ? '88px' : '110px'">
        <el-form-item label="名称"><el-input v-model="quickRemote.name" placeholder="办公室群晖" /></el-form-item>
        <el-form-item label="地址">
          <div class="pair-row">
            <el-input class="grow" v-model="quickRemote.host" placeholder="192.168.1.5" />
            <el-input-number class="port" v-model="quickRemote.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="HTTPS">
          <el-switch v-model="quickRemote.https" @change="onQuickHttpsChange" />
          <span class="muted" style="margin-left:10px">HTTP 5000 / HTTPS 5001</span>
        </el-form-item>
        <el-form-item label="账号"><el-input v-model="quickRemote.username" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="quickRemote.password" type="password" show-password placeholder="必填" />
        </el-form-item>
        <el-form-item label="远程目录">
          <el-input v-model="quickRemote.remote_dir" placeholder="/sql_backup" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="quickRemoteDlg = false">取消</el-button>
        <el-button type="primary" :loading="quickRemoteSaving" @click="saveQuickRemote">保存并选用</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="quickSshDlg" title="新增 SSH 代理" width="520px" :close-on-click-modal="false" append-to-body>
      <el-form :model="quickSsh" :label-width="narrow ? '88px' : '110px'">
        <el-form-item label="名称"><el-input v-model="quickSsh.name" placeholder="办公室跳板" /></el-form-item>
        <el-form-item label="地址">
          <div class="pair-row">
            <el-input class="grow" v-model="quickSsh.host" placeholder="andy.example.com" />
            <el-input-number class="port" v-model="quickSsh.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="用户"><el-input v-model="quickSsh.username" placeholder="billy" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="quickSsh.password" type="password" show-password placeholder="与密钥二选一" />
        </el-form-item>
        <el-form-item label="私钥">
          <el-input v-model="quickSsh.key" type="textarea" :rows="3" placeholder="私钥内容，优先于密码" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="quickSshDlg = false">取消</el-button>
        <el-button type="primary" :loading="quickSshSaving" @click="saveQuickSsh">保存并选用</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="runDlg" title="立即备份" width="460px" :close-on-click-modal="false">
      <el-form label-width="100px">
        <el-form-item label="备份类型">
          <el-select v-model="runForm.backup_type" style="width:100%">
            <el-option label="完整备份 Full" value="full" />
            <el-option label="差异备份 Differential" value="diff" />
            <el-option label="日志备份 Log" value="log" />
          </el-select>
          <div class="muted">差异需要已有完整备份；SIMPLE 恢复模式不能做日志备份。备份后会自动校验。</div>
        </el-form-item>
        <el-form-item label="保留天数">
          <el-input-number v-model="runForm.retain_days" :min="1" :max="3650" />
          <div class="muted">只留最近 N 天的日期目录。例如 2 = 今天和昨天，更早的会从 SQL Server 和群晖删除。</div>
        </el-form-item>
        <el-form-item label="压缩"><el-switch v-model="runForm.compress" /></el-form-item>
        <el-form-item label="删除旧备份"><el-switch v-model="runForm.delete_old" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runDlg = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="doRun">开始备份</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { isAdmin, isSystemDb, parseDbNames } from "../format";
import { useBreakpoints } from "../useBreakpoints";

const router = useRouter();
const { narrow } = useBreakpoints();

const admin = isAdmin();
const opsWidth = computed(() => {
  if (admin) return narrow.value ? 208 : 288;
  return narrow.value ? 132 : 128;
});
const loading = ref(false);
const saving = ref(false);
const testing = ref(false);
const running = ref(false);
const items = ref([]);
const dlg = ref(false);
const runDlg = ref(false);
const runCid = ref(0);
const form = reactive(emptyForm());
const dbOptions = ref([]);
const selectedDbs = ref([]);
const runForm = reactive({ backup_type: "full", compress: true, retain_days: 7, delete_old: true });
const remotes = ref([]);
const proxies = ref([]);
const quickRemoteDlg = ref(false);
const quickSshDlg = ref(false);
const quickRemoteSaving = ref(false);
const quickSshSaving = ref(false);
const quickRemote = reactive(emptyRemote());
const quickSsh = reactive(emptySsh());
const backupStates = reactive({});
const backupTimers = new Map();

function emptyRemote() {
  return {
    name: "",
    host: "",
    port: 5001,
    https: true,
    username: "",
    password: "",
    remote_dir: "/sql_backup",
  };
}

function emptySsh() {
  return {
    name: "",
    host: "",
    port: 22,
    username: "",
    password: "",
    key: "",
  };
}

function emptyForm() {
  return {
    id: null,
    name: "",
    db_type: "sqlserver",
    host: "",
    port: 1433,
    database: "",
    username: "sa",
    password: "",
    connect_mode: "direct",
    backup_dir: "",
    remote_enabled: false,
    remote_target_id: null,
    ssh_proxy_id: null,
  };
}

function dbTagItems(row) {
  return parseDbNames(row && row.database);
}

const SYSTEM_DBS = new Set(["master", "tempdb", "model", "msdb"]);

function asDbItem(n) {
  if (n && typeof n === "object") {
    return { name: n.name, is_system: !!n.is_system };
  }
  const name = String(n || "").trim();
  return { name, is_system: SYSTEM_DBS.has(name.toLowerCase()) };
}

function userDbNames() {
  return dbOptions.value.filter((d) => !d.is_system).map((d) => d.name);
}

function allDbNames() {
  return dbOptions.value.map((d) => d.name);
}

function selectAll() {
  selectedDbs.value = allDbNames();
}

function selectUserOnly() {
  selectedDbs.value = userDbNames();
}

function databasePayload() {
  if (!dbOptions.value.length) return String(form.database || "").trim();
  if (!selectedDbs.value.length) return "";
  return selectedDbs.value.join(",");
}

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get("/connections");
    items.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}

function openEdit(row) {
  Object.assign(form, emptyForm(), row || {}, { password: "" });
  form.remote_enabled = !!(row && row.remote_enabled);
  form.remote_target_id = row && row.remote_target_id ? row.remote_target_id : null;
  form.ssh_proxy_id = row && row.ssh_proxy_id ? row.ssh_proxy_id : null;
  const names = String(row?.database || "")
    .replace(/;/g, ",")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  dbOptions.value = names.map(asDbItem);
  selectedDbs.value = names.length ? [...names] : [];
  dlg.value = true;
}

async function probe() {
  if (!form.host || !form.username) {
    ElMessage.warning("请先填写地址和用户名");
    return;
  }
  if (!form.id && !form.password) {
    ElMessage.warning("请先填写数据库密码");
    return;
  }
  if (form.connect_mode === "ssh" && !form.ssh_proxy_id) {
    ElMessage.warning("SSH 模式请选择跳板代理");
    return;
  }
  testing.value = true;
  try {
    const { data } = await http.post("/connections/probe", {
      id: form.id || null,
      host: form.host,
      port: form.port,
      username: form.username,
      password: form.password,
      connect_mode: form.connect_mode,
      ssh_proxy_id: form.ssh_proxy_id || 0,
    });
    const live = (data.databases || []).map(asDbItem);
    dbOptions.value = live;
    const prev = selectedDbs.value;
    const liveNames = live.map((d) => d.name);
    const userNames = live.filter((d) => !d.is_system).map((d) => d.name);
    if (prev.length) {
      const keep = prev.filter((n) => liveNames.includes(n));
      selectedDbs.value = keep.length ? keep : userNames;
    } else {
      selectedDbs.value = userNames;
    }
    ElMessage.success(data.message || `连接成功，发现 ${live.length} 个数据库`);
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    testing.value = false;
  }
}

async function save() {
  if (form.remote_enabled && !form.remote_target_id) {
    ElMessage.warning("已开启远程备份，请选择群晖配置");
    return;
  }
  if (form.connect_mode === "ssh" && !form.ssh_proxy_id) {
    ElMessage.warning("SSH 模式请选择跳板代理");
    return;
  }
  saving.value = true;
  try {
    const payload = { ...form, database: databasePayload() };
    payload.remote_target_id = form.remote_enabled ? Number(form.remote_target_id || 0) : 0;
    payload.ssh_proxy_id = form.connect_mode === "ssh" ? Number(form.ssh_proxy_id || 0) : 0;
    delete payload.id;
    delete payload.has_password;
    delete payload.has_ssh_password;
    delete payload.has_ssh_key;
    delete payload.created_at;
    delete payload.database_label;
    delete payload.remote_target_name;
    delete payload.ssh_proxy_name;
    delete payload.ssh_host;
    delete payload.ssh_port;
    delete payload.ssh_user;
    if (form.id) await http.put(`/connections/${form.id}`, payload);
    else await http.post("/connections", payload);
    ElMessage.success("已保存");
    dlg.value = false;
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    saving.value = false;
  }
}

async function remove(row) {
  await ElMessageBox.confirm(
    `确认删除连接「${row.name}」？将同时删除该连接下的全部定时任务和备份历史。SQL Server 本机上的 .bak 不会删。`,
    "删除连接",
    { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
  );
  try {
    await http.delete(`/connections/${row.id}`);
    ElMessage.success("已删除");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

function openBackups(row) {
  router.push({ path: "/backups", query: { connection_id: String(row.id) } });
}

function openRun(row) {
  runCid.value = row.id;
  runForm.backup_type = "full";
  runForm.compress = true;
  runForm.retain_days = 7;
  runForm.delete_old = true;
  runDlg.value = true;
}

async function doRun() {
  const cid = runCid.value;
  if (!cid || backupStates[cid]?.status === "running") return;
  const payload = { ...runForm };
  runDlg.value = false;
  running.value = false;
  startProgress(cid);
  try {
    await http.post(`/backups/run/${cid}`, payload);
    pollProgress(cid);
  } catch (e) {
    finishProgress(cid, "failed", errMsg(e));
    ElMessage.error(errMsg(e));
  }
}

function startProgress(cid) {
  stopTimer(cid);
  backupStates[cid] = {
    percent: 1,
    status: "running",
    message: "正在准备备份",
    current_db: "",
    done: 0,
    total: 1,
  };
}

function applyJob(item, toast) {
  const cid = item && item.connection_id;
  if (!cid) return;
  const prev = backupStates[cid]?.status;
  backupStates[cid] = {
    percent: Number(item.percent) || 0,
    status: item.status || "running",
    message: item.message || "",
    current_db: item.current_db || "",
    done: item.done || 0,
    total: item.total || 1,
    kind: item.kind || "backup",
  };
  if (item.status === "success") {
    stopTimer(cid);
    if (toast && prev === "running") ElMessage.success(item.message || "备份完成");
    window.setTimeout(() => {
      if (backupStates[cid]?.status === "success") delete backupStates[cid];
    }, 4000);
  } else if (item.status === "failed") {
    stopTimer(cid);
    if (toast && prev === "running") ElMessage.error(item.message || "备份失败");
    window.setTimeout(() => {
      if (backupStates[cid]?.status === "failed") delete backupStates[cid];
    }, 8000);
  }
}

function pollProgress(cid) {
  stopTimer(cid);
  const tick = async () => {
    try {
      const { data } = await http.get(`/backups/progress/${cid}`);
      if (!data.item) return;
      applyJob(data.item, true);
    } catch {
      /* 备份线程仍在跑时瞬时 500 不要当成失败，下一轮继续问 */
    }
  };
  tick();
  backupTimers.set(cid, window.setInterval(tick, 800));
}

async function restoreProgress() {
  try {
    const { data } = await http.get("/backups/progress");
    for (const item of data.items || []) {
      applyJob(item, false);
      if (item.status === "running") pollProgress(item.connection_id);
    }
  } catch {
    /* 旧服务端可能没有该接口 */
  }
}

function stopTimer(cid) {
  const timer = backupTimers.get(cid);
  if (timer) window.clearInterval(timer);
  backupTimers.delete(cid);
}

function finishProgress(cid, status, message) {
  stopTimer(cid);
  const state = backupStates[cid] || { percent: 0 };
  state.status = status;
  state.percent = status === "success" ? 100 : state.percent || 8;
  state.message = message || (status === "success" ? "备份完成" : "备份失败");
  backupStates[cid] = state;
  window.setTimeout(() => {
    delete backupStates[cid];
  }, status === "success" ? 4000 : 8000);
}

function progressText(state) {
  const verb = state.kind === "restore" ? "恢复" : "备份";
  if (state.status === "success") return "已完成 100%";
  if (state.status === "failed") return state.message || `${verb}失败`;
  if (state.message && /上传|群晖/.test(state.message)) {
    return `${state.message} ${state.percent || 98}%`;
  }
  if (state.current_db) return `${state.current_db} ${state.percent}%`;
  return `${verb}中 ${state.percent}%`;
}

function openQuickRemote() {
  Object.assign(quickRemote, emptyRemote());
  quickRemoteDlg.value = true;
}

function onQuickHttpsChange(on) {
  if (on && Number(quickRemote.port) === 5000) quickRemote.port = 5001;
  if (!on && Number(quickRemote.port) === 5001) quickRemote.port = 5000;
}

async function saveQuickRemote() {
  if (!quickRemote.name || !quickRemote.host || !quickRemote.username) {
    ElMessage.warning("请填写名称、地址和账号");
    return;
  }
  if (!quickRemote.password) {
    ElMessage.warning("请填写群晖密码");
    return;
  }
  quickRemoteSaving.value = true;
  try {
    const { data } = await http.post("/remote-targets", { ...quickRemote });
    const item = data.item || {};
    ElMessage.success("已保存");
    quickRemoteDlg.value = false;
    await loadRemotes();
    if (item.id) form.remote_target_id = item.id;
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    quickRemoteSaving.value = false;
  }
}

function openQuickSsh() {
  Object.assign(quickSsh, emptySsh());
  quickSshDlg.value = true;
}

async function saveQuickSsh() {
  if (!quickSsh.name || !quickSsh.host || !quickSsh.username) {
    ElMessage.warning("请填写名称、地址和用户");
    return;
  }
  if (!quickSsh.password && !quickSsh.key) {
    ElMessage.warning("请填写 SSH 密码或私钥");
    return;
  }
  quickSshSaving.value = true;
  try {
    const { data } = await http.post("/ssh-proxies", { ...quickSsh });
    const item = data.item || {};
    ElMessage.success("已保存");
    quickSshDlg.value = false;
    await loadProxies();
    if (item.id) form.ssh_proxy_id = item.id;
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    quickSshSaving.value = false;
  }
}

async function loadRemotes() {
  try {
    const { data } = await http.get("/remote-targets");
    remotes.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function loadProxies() {
  try {
    const { data } = await http.get("/ssh-proxies");
    proxies.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(() => {
  load();
  loadRemotes();
  loadProxies();
  restoreProgress();
});

onBeforeUnmount(() => {
  backupTimers.forEach((timer) => window.clearInterval(timer));
  backupTimers.clear();
});
</script>

<style scoped>
.db-box {
  width: 100%;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 8px 10px 4px;
  background: #fafafa;
}
.db-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 4px;
}
.db-group {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 10px;
  max-height: 240px;
  overflow: auto;
  padding: 4px 0 8px;
}
@media (max-width: 720px) {
  .db-group { grid-template-columns: 1fr; }
  .db-toolbar { flex-wrap: wrap; }
}
.db-group :deep(.el-checkbox) {
  margin: 0;
  display: flex;
  align-items: center;
  min-width: 0;
  height: 28px;
  white-space: nowrap;
}
.db-group :deep(.el-checkbox__label) {
  display: flex;
  align-items: center;
  min-width: 0;
  padding-left: 6px;
  overflow: hidden;
}
.db-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sys-tag {
  margin-left: 4px;
  flex-shrink: 0;
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
