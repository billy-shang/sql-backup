<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>数据库连接</h2>
        <div class="muted">平台可部署在任意机器。配置好连接后，备份文件写在 SQL Server 所在服务器上，不会写到本平台。</div>
      </div>
        <div style="display:flex;gap:8px">
          <el-button v-if="admin" @click="openRemoteDlg">远程备份配置</el-button>
          <el-button v-if="admin" type="primary" @click="openEdit()">新增连接</el-button>
        </div>
    </div>
    <el-table :data="items" stripe v-loading="loading">
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="db_type" label="类型" width="110" />
      <el-table-column label="地址" min-width="170">
        <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
      </el-table-column>
      <el-table-column label="数据库" min-width="180" show-overflow-tooltip>
        <template #default="{ row }">{{ row.database_label || dbLabel(row.database) }}</template>
      </el-table-column>
      <el-table-column prop="username" label="用户名" width="110" />
      <el-table-column label="连接方式" width="110">
        <template #default="{ row }">{{ modeMap[row.connect_mode] || row.connect_mode }}</template>
      </el-table-column>
      <el-table-column prop="backup_dir" label="服务器备份目录" min-width="160" show-overflow-tooltip />
      <el-table-column label="远程备份" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">{{ row.remote_enabled ? (row.remote_target_name || "已开启") : "否" }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="170">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="220" fixed="right">
        <template #default="{ row }">
          <el-button text type="success" @click="openRun(row)">立即备份</el-button>
          <el-button v-if="admin" text @click="openEdit(row)">编辑</el-button>
          <el-button v-if="admin" text type="danger" @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑连接' : '新增连接'" width="720px">
      <el-form :model="form" label-width="110px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="生产SQLServer" /></el-form-item>
        <el-form-item label="数据库类型">
          <el-select v-model="form.db_type" style="width:100%">
            <el-option label="SQL Server" value="sqlserver" />
          </el-select>
        </el-form-item>
        <el-form-item label="地址"><el-input v-model="form.host" placeholder="192.168.1.10" /></el-form-item>
        <el-form-item label="端口"><el-input-number v-model="form.port" :min="1" :max="65535" /></el-form-item>
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
            <el-radio value="direct">直连（平台能访问 SQL 端口）</el-radio>
            <el-radio value="ssh">SSH 代理（经跳板访问 SQL）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="服务器备份目录">
          <el-input v-model="form.backup_dir" placeholder="留空=SQL Server 默认 Backup 目录" />
          <div class="muted">
            填 SQL Server 那台机器上的路径，不是本平台路径。留空则用实例默认 Backup 目录。也可指定该机其它盘，例如 D:\TEST。文件在「目录\库名\日期\」子目录里，不会出现在 D:\TEST 根目录。
          </div>
        </el-form-item>
        <el-form-item label="是否远程备份">
          <el-switch v-model="form.remote_enabled" />
        </el-form-item>
        <el-form-item v-if="form.remote_enabled" label="远程备份">
          <el-select v-model="form.remote_target_id" placeholder="选择群晖配置" style="width:100%" clearable>
            <el-option v-for="t in remotes" :key="t.id" :label="`${t.name}（${t.host}）`" :value="t.id" />
          </el-select>
          <div class="muted">本地备份完成后，把 .bak 上传到所选群晖的远程目录。</div>
        </el-form-item>
        <template v-if="form.connect_mode === 'ssh'">
          <el-form-item label="SSH 地址"><el-input v-model="form.ssh_host" /></el-form-item>
          <el-form-item label="SSH 端口"><el-input-number v-model="form.ssh_port" :min="1" :max="65535" /></el-form-item>
          <el-form-item label="SSH 用户"><el-input v-model="form.ssh_user" /></el-form-item>
          <el-form-item label="SSH 密码">
            <el-input v-model="form.ssh_password" type="password" show-password placeholder="与密钥二选一" />
          </el-form-item>
          <el-form-item label="SSH 密钥">
            <el-input v-model="form.ssh_key" type="textarea" :rows="4" placeholder="私钥内容，优先于密码" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button type="success" :loading="testing" @click="probe">测试</el-button>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="runDlg" title="立即备份" width="460px">
      <el-form label-width="100px">
        <el-form-item label="备份类型">
          <el-select v-model="runForm.backup_type" style="width:100%">
            <el-option label="完整备份 Full" value="full" />
            <el-option label="差异备份 Differential" value="diff" />
            <el-option label="日志备份 Log" value="log" />
          </el-select>
        </el-form-item>
        <el-form-item label="保留天数"><el-input-number v-model="runForm.retain_days" :min="1" :max="3650" /></el-form-item>
        <el-form-item label="压缩"><el-switch v-model="runForm.compress" /></el-form-item>
        <el-form-item label="删除旧备份"><el-switch v-model="runForm.delete_old" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="runDlg = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="doRun">开始备份</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="remoteDlg" title="远程备份配置（群晖）" width="760px">
      <div class="page-head" style="margin-bottom:12px">
        <div class="muted">配置群晖地址、账号、密码和远程目录。连接里勾选「是否远程备份」后即可选用。</div>
        <el-button type="primary" @click="openRemoteEdit()">新增群晖</el-button>
      </div>
      <el-table :data="remotes" stripe>
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column label="地址" min-width="160">
          <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
        </el-table-column>
        <el-table-column prop="username" label="账号" width="110" />
        <el-table-column prop="remote_dir" label="远程目录" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button text @click="openRemoteEdit(row)">编辑</el-button>
            <el-button text type="danger" @click="removeRemote(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="remoteEditDlg" :title="remoteForm.id ? '编辑群晖' : '新增群晖'" width="560px">
      <el-form :model="remoteForm" label-width="110px">
        <el-form-item label="名称"><el-input v-model="remoteForm.name" placeholder="办公室群晖" /></el-form-item>
        <el-form-item label="地址"><el-input v-model="remoteForm.host" placeholder="192.168.1.5" /></el-form-item>
        <el-form-item label="端口"><el-input-number v-model="remoteForm.port" :min="1" :max="65535" /></el-form-item>
        <el-form-item label="HTTPS"><el-switch v-model="remoteForm.https" /></el-form-item>
        <el-form-item label="账号"><el-input v-model="remoteForm.username" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="remoteForm.password" type="password" show-password :placeholder="remoteForm.id ? '留空则使用已保存密码' : '必填'" />
        </el-form-item>
        <el-form-item label="远程目录">
          <el-input v-model="remoteForm.remote_dir" placeholder="/sql_backup" />
          <div class="muted">群晖 File Station 中的目录，例如 /fileserver/DB_BackUP。备份会放到 目录/连接名/库名/日期/ 下。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="success" :loading="remoteTesting" @click="probeRemote">测试</el-button>
        <el-button @click="remoteEditDlg = false">取消</el-button>
        <el-button type="primary" :loading="remoteSaving" @click="saveRemote">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { dbLabel, fmtTime, isAdmin, modeMap } from "../format";

const admin = isAdmin();
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
const remoteDlg = ref(false);
const remoteEditDlg = ref(false);
const remoteSaving = ref(false);
const remoteTesting = ref(false);
const remoteForm = reactive(emptyRemote());

function emptyRemote() {
  return {
    id: null,
    name: "",
    host: "",
    port: 5001,
    https: true,
    username: "",
    password: "",
    remote_dir: "/sql_backup",
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
    ssh_host: "",
    ssh_port: 22,
    ssh_user: "",
    ssh_password: "",
    ssh_key: "",
  };
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

function sameSet(a, b) {
  if (a.length !== b.length) return false;
  const s = new Set(b);
  return a.every((x) => s.has(x));
}

function databasePayload() {
  if (!dbOptions.value.length) return "";
  if (!selectedDbs.value.length) return "";
  if (sameSet(selectedDbs.value, userDbNames())) return "";
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
  Object.assign(form, emptyForm(), row || {}, { password: "", ssh_password: "", ssh_key: "" });
  form.remote_enabled = !!(row && row.remote_enabled);
  form.remote_target_id = row && row.remote_target_id ? row.remote_target_id : null;
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
  if (form.connect_mode === "ssh" && !form.ssh_user && !form.id) {
    ElMessage.warning("SSH 模式请填写 SSH 用户名");
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
      ssh_host: form.ssh_host,
      ssh_port: form.ssh_port,
      ssh_user: form.ssh_user,
      ssh_password: form.ssh_password,
      ssh_key: form.ssh_key,
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
  saving.value = true;
  try {
    const payload = { ...form, database: databasePayload() };
    payload.remote_target_id = form.remote_enabled ? Number(form.remote_target_id || 0) : 0;
    delete payload.id;
    delete payload.has_password;
    delete payload.has_ssh_password;
    delete payload.has_ssh_key;
    delete payload.created_at;
    delete payload.database_label;
    delete payload.remote_target_name;
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
  await ElMessageBox.confirm(`确认删除连接「${row.name}」？`, "删除", { type: "warning" });
  try {
    await http.delete(`/connections/${row.id}`);
    ElMessage.success("已删除");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
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
  running.value = true;
  try {
    const { data } = await http.post(`/backups/run/${runCid.value}`, runForm);
    ElMessageBox.alert(data.message || "备份完成", "备份已写入数据库服务器", { type: "success" });
    runDlg.value = false;
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    running.value = false;
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

function openRemoteDlg() {
  loadRemotes();
  remoteDlg.value = true;
}

function openRemoteEdit(row) {
  Object.assign(remoteForm, emptyRemote(), row || {}, { password: "" });
  remoteEditDlg.value = true;
}

async function probeRemote() {
  if (!remoteForm.host || !remoteForm.username) {
    ElMessage.warning("请填写地址和账号");
    return;
  }
  if (!remoteForm.id && !remoteForm.password) {
    ElMessage.warning("请填写群晖密码");
    return;
  }
  remoteTesting.value = true;
  try {
    const { data } = await http.post("/remote-targets/probe", remoteForm);
    ElMessage.success(data.message || "连接成功");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    remoteTesting.value = false;
  }
}

async function saveRemote() {
  if (!remoteForm.name || !remoteForm.host || !remoteForm.username) {
    ElMessage.warning("请填写名称、地址和账号");
    return;
  }
  if (!remoteForm.id && !remoteForm.password) {
    ElMessage.warning("请填写群晖密码");
    return;
  }
  remoteSaving.value = true;
  try {
    const payload = { ...remoteForm };
    delete payload.id;
    delete payload.has_password;
    delete payload.created_at;
    if (remoteForm.id) await http.put(`/remote-targets/${remoteForm.id}`, payload);
    else await http.post("/remote-targets", payload);
    ElMessage.success("已保存");
    remoteEditDlg.value = false;
    loadRemotes();
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    remoteSaving.value = false;
  }
}

async function removeRemote(row) {
  await ElMessageBox.confirm(`确认删除群晖配置「${row.name}」？`, "删除", { type: "warning" });
  try {
    await http.delete(`/remote-targets/${row.id}`);
    ElMessage.success("已删除");
    loadRemotes();
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(() => {
  load();
  loadRemotes();
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
</style>
