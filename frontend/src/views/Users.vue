<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>用户管理</h2>
        <div class="muted">管理员拥有全部权限；普通运维可查看、执行备份、下载，不能改配置</div>
      </div>
      <el-button type="primary" @click="open()">新增用户</el-button>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无用户">
      <el-table-column prop="username" label="用户名" :min-width="narrow ? 100 : 160" />
      <el-table-column label="角色" :min-width="narrow ? 88 : 120">
        <template #default="{ row }">{{ row.role === "admin" ? "管理员" : "普通运维" }}</template>
      </el-table-column>
      <el-table-column v-if="!narrow" label="创建时间" min-width="180">
        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" class-name="ops-col" align="left" fixed="right" :width="narrow ? 136 : 148">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text @click="openReset(row)">重置密码</el-button>
            <el-button v-if="row.id !== meId" text type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="resetDlg" title="重置密码" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="用户">{{ resetUser.username }}</el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="resetPassword" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetDlg = false">取消</el-button>
        <el-button type="primary" :loading="resetting" @click="saveReset">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="dlg" title="新增用户" width="420px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" style="width:100%">
            <el-option label="管理员" value="admin" />
            <el-option label="普通运维" value="operator" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { fmtTime } from "../format";
import { useBreakpoints } from "../useBreakpoints";

const { narrow } = useBreakpoints();
const meId = (() => {
  try {
    return Number(JSON.parse(localStorage.getItem("sqlbackup-user") || "{}").id || 0);
  } catch {
    return 0;
  }
})();
const loading = ref(false);
const saving = ref(false);
const items = ref([]);
const dlg = ref(false);
const form = reactive({ username: "", password: "", role: "operator" });
const resetDlg = ref(false);
const resetting = ref(false);
const resetUser = reactive({ id: 0, username: "" });
const resetPassword = ref("");

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get("/users");
    items.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}

function open() {
  form.username = "";
  form.password = "";
  form.role = "operator";
  dlg.value = true;
}

async function save() {
  saving.value = true;
  try {
    await http.post("/users", form);
    ElMessage.success("已创建");
    dlg.value = false;
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    saving.value = false;
  }
}

function openReset(row) {
  resetUser.id = row.id;
  resetUser.username = row.username;
  resetPassword.value = "";
  resetDlg.value = true;
}

async function saveReset() {
  if ((resetPassword.value || "").trim().length < 6) {
    ElMessage.warning("新密码至少 6 位");
    return;
  }
  resetting.value = true;
  try {
    await http.put(`/users/${resetUser.id}/password`, { password: resetPassword.value });
    ElMessage.success("密码已重置");
    resetDlg.value = false;
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    resetting.value = false;
  }
}

async function remove(row) {
  await ElMessageBox.confirm(`删除用户「${row.username}」？`, "删除", { type: "warning" });
  try {
    await http.delete(`/users/${row.id}`);
    ElMessage.success("已删除");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

onMounted(load);
</script>
