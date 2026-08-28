<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>配置中心</h2>
        <div class="muted">{{ admin ? "群晖、SSH 跳板和通知都在这里。连接里下拉选用，不用再手填。" : "备份完成后可向飞书、企业微信、钉钉推送结果。" }}</div>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane v-if="admin" label="群晖备份" name="remote">
        <div class="tab-toolbar">
          <div class="muted">群晖 File Station 地址、账号和远程目录。连接里勾选「是否群晖备份」后即可选用。</div>
          <el-button type="primary" @click="openRemoteEdit()">新增群晖</el-button>
        </div>
        <el-table :data="remotes" stripe v-loading="remoteLoading" class="fit-table" table-layout="fixed" empty-text="暂无群晖配置">
          <el-table-column prop="name" label="名称" :min-width="narrow ? 100 : 120" show-overflow-tooltip />
          <el-table-column v-if="!narrow" label="地址" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
          </el-table-column>
          <el-table-column v-if="!narrow" prop="username" label="账号" width="100" min-width="100" show-overflow-tooltip />
          <el-table-column prop="remote_dir" label="远程目录" :min-width="narrow ? 140 : 180" show-overflow-tooltip />
          <el-table-column label="操作" class-name="ops-col" align="left" width="140" min-width="140">
            <template #default="{ row }">
              <div class="ops-cell">
                <el-button text @click="openRemoteEdit(row)">编辑</el-button>
                <el-button text type="danger" @click="removeRemote(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane v-if="admin" label="SSH 代理" name="ssh">
        <div class="tab-toolbar">
          <div class="muted">跳板机地址、账号、密码或私钥。连接方式选「SSH 代理」后即可选用。</div>
          <el-button type="primary" @click="openSshEdit()">新增代理</el-button>
        </div>
        <el-table :data="proxies" stripe v-loading="sshLoading" class="fit-table" table-layout="fixed" empty-text="暂无 SSH 代理">
          <el-table-column prop="name" label="名称" :min-width="narrow ? 100 : 120" show-overflow-tooltip />
          <el-table-column v-if="!narrow" label="地址" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">{{ row.host }}:{{ row.port }}</template>
          </el-table-column>
          <el-table-column prop="username" label="用户" :width="narrow ? 90 : 100" show-overflow-tooltip />
          <el-table-column v-if="!narrow" label="认证" width="80" min-width="80">
            <template #default="{ row }">{{ row.has_key ? "私钥" : row.has_password ? "密码" : "—" }}</template>
          </el-table-column>
          <el-table-column label="操作" class-name="ops-col" align="left" width="140" min-width="140">
            <template #default="{ row }">
              <div class="ops-cell">
                <el-button text @click="openSshEdit(row)">编辑</el-button>
                <el-button text type="danger" @click="removeSsh(row)">删除</el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane label="通知" name="notify">
        <div class="tab-toolbar">
          <div class="muted">备份完成后可向飞书、企业微信、钉钉推送结果，可单选或同时启用。</div>
        </div>
        <NotifyPanel />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="remoteEditDlg" :title="remoteForm.id ? '编辑群晖' : '新增群晖'" width="560px" :close-on-click-modal="false">
      <el-form :model="remoteForm" :label-width="narrow ? '88px' : '110px'">
        <el-form-item label="名称"><el-input v-model="remoteForm.name" placeholder="办公室群晖" /></el-form-item>
        <el-form-item label="地址">
          <div class="pair-row">
            <el-input class="grow" v-model="remoteForm.host" placeholder="192.168.1.5" />
            <el-input-number class="port" v-model="remoteForm.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="HTTPS">
          <el-switch v-model="remoteForm.https" @change="onRemoteHttpsChange" />
          <span class="muted" style="margin-left:10px">HTTP 一般 5000，HTTPS 一般 5001</span>
        </el-form-item>
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

    <el-dialog v-model="sshEditDlg" :title="sshForm.id ? '编辑 SSH 代理' : '新增 SSH 代理'" width="560px" :close-on-click-modal="false">
      <el-form :model="sshForm" :label-width="narrow ? '88px' : '110px'">
        <el-form-item label="名称"><el-input v-model="sshForm.name" placeholder="办公室跳板" /></el-form-item>
        <el-form-item label="地址">
          <div class="pair-row">
            <el-input class="grow" v-model="sshForm.host" placeholder="andy.example.com" />
            <el-input-number class="port" v-model="sshForm.port" :min="1" :max="65535" controls-position="right" />
          </div>
        </el-form-item>
        <el-form-item label="用户"><el-input v-model="sshForm.username" placeholder="billy" /></el-form-item>
        <el-form-item label="密码">
          <el-input v-model="sshForm.password" type="password" show-password :placeholder="sshForm.id ? '留空则使用已保存密码' : '与密钥二选一'" />
        </el-form-item>
        <el-form-item label="私钥">
          <el-input v-model="sshForm.key" type="textarea" :rows="4" :placeholder="sshForm.id ? '留空则使用已保存密钥' : '私钥内容，优先于密码'" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="success" :loading="sshTesting" @click="probeSsh">测试</el-button>
        <el-button @click="sshEditDlg = false">取消</el-button>
        <el-button type="primary" :loading="sshSaving" @click="saveSsh">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { isAdmin } from "../format";
import { useBreakpoints } from "../useBreakpoints";
import NotifyPanel from "./Notify.vue";

const admin = isAdmin();
const { narrow } = useBreakpoints();
const route = useRoute();
const tab = ref(admin ? "remote" : "notify");

function applyTab(raw) {
  const allowed = admin ? ["remote", "ssh", "notify"] : ["notify"];
  tab.value = allowed.includes(raw) ? raw : allowed[0];
}

watch(() => route.query.tab, (v) => applyTab(v), { immediate: true });
const remotes = ref([]);
const proxies = ref([]);
const remoteLoading = ref(false);
const sshLoading = ref(false);
const remoteEditDlg = ref(false);
const sshEditDlg = ref(false);
const remoteSaving = ref(false);
const remoteTesting = ref(false);
const sshSaving = ref(false);
const sshTesting = ref(false);
const remoteForm = reactive(emptyRemote());
const sshForm = reactive(emptySsh());

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

function emptySsh() {
  return {
    id: null,
    name: "",
    host: "",
    port: 22,
    username: "",
    password: "",
    key: "",
  };
}

async function loadRemotes() {
  remoteLoading.value = true;
  try {
    const { data } = await http.get("/remote-targets");
    remotes.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    remoteLoading.value = false;
  }
}

async function loadProxies() {
  sshLoading.value = true;
  try {
    const { data } = await http.get("/ssh-proxies");
    proxies.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    sshLoading.value = false;
  }
}

function openRemoteEdit(row) {
  Object.assign(remoteForm, emptyRemote(), row || {}, { password: "" });
  remoteEditDlg.value = true;
}

function onRemoteHttpsChange(on) {
  if (on && Number(remoteForm.port) === 5000) remoteForm.port = 5001;
  if (!on && Number(remoteForm.port) === 5001) remoteForm.port = 5000;
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
  await ElMessageBox.confirm(`确认删除群晖配置「${row.name}」？已选用的连接会关闭群晖备份。`, "删除", {
    type: "warning",
    confirmButtonText: "删除",
    cancelButtonText: "取消",
  });
  try {
    await http.delete(`/remote-targets/${row.id}`);
    ElMessage.success("已删除");
    loadRemotes();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

function openSshEdit(row) {
  Object.assign(sshForm, emptySsh(), row || {}, { password: "", key: "" });
  sshEditDlg.value = true;
}

async function probeSsh() {
  if (!sshForm.host || !sshForm.username) {
    ElMessage.warning("请填写地址和用户");
    return;
  }
  if (!sshForm.id && !sshForm.password && !sshForm.key) {
    ElMessage.warning("请填写 SSH 密码或私钥");
    return;
  }
  sshTesting.value = true;
  try {
    const { data } = await http.post("/ssh-proxies/probe", sshForm);
    ElMessage.success(data.message || "SSH 连接成功");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    sshTesting.value = false;
  }
}

async function saveSsh() {
  if (!sshForm.name || !sshForm.host || !sshForm.username) {
    ElMessage.warning("请填写名称、地址和用户");
    return;
  }
  if (!sshForm.id && !sshForm.password && !sshForm.key) {
    ElMessage.warning("请填写 SSH 密码或私钥");
    return;
  }
  sshSaving.value = true;
  try {
    const payload = { ...sshForm };
    delete payload.id;
    delete payload.has_password;
    delete payload.has_key;
    delete payload.created_at;
    if (sshForm.id) await http.put(`/ssh-proxies/${sshForm.id}`, payload);
    else await http.post("/ssh-proxies", payload);
    ElMessage.success("已保存");
    sshEditDlg.value = false;
    loadProxies();
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    sshSaving.value = false;
  }
}

async function removeSsh(row) {
  await ElMessageBox.confirm(`确认删除 SSH 代理「${row.name}」？仍被连接使用时不能删。`, "删除", {
    type: "warning",
    confirmButtonText: "删除",
    cancelButtonText: "取消",
  });
  try {
    await http.delete(`/ssh-proxies/${row.id}`);
    ElMessage.success("已删除");
    loadProxies();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(() => {
  if (admin) {
    loadRemotes();
    loadProxies();
  }
});
</script>
