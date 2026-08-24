<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>定时任务</h2>
        <div class="muted">每天 / 每周 / 指定时间。可立即执行一次，进度在「数据库连接」页查看。</div>
      </div>
      <el-button v-if="admin" type="primary" @click="openEdit()">新增任务</el-button>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无定时任务">
      <el-table-column prop="name" label="任务" show-overflow-tooltip />
      <el-table-column prop="connection_name" label="连接" show-overflow-tooltip />
      <el-table-column label="周期" width="128" show-overflow-tooltip>
        <template #default="{ row }">{{ cycleText(row) }}</template>
      </el-table-column>
      <el-table-column label="类型" width="64">
        <template #default="{ row }">{{ typeMap[row.backup_type] }}</template>
      </el-table-column>
      <el-table-column prop="retain_days" label="保留" width="64" />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag
            :type="statusType(row.last_status)"
            size="small"
            class="click-tag"
            :title="row.last_error || ''"
            @click="showError(row)"
          >{{ statusText(row.last_status) || (row.enabled ? "待运行" : "暂停") }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="上次" width="136">
        <template #default="{ row }">{{ fmtTimeShort(row.last_run_at) }}</template>
      </el-table-column>
      <el-table-column label="下次" width="136">
        <template #default="{ row }">{{ row.enabled ? fmtTimeShort(row.next_run_at) : "—" }}</template>
      </el-table-column>
      <el-table-column v-if="admin" label="操作" class-name="ops-col" width="248">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text type="success" @click="runNow(row)">执行</el-button>
            <el-button v-if="row.enabled" text @click="pause(row)">暂停</el-button>
            <el-button v-else text type="success" @click="resume(row)">恢复</el-button>
            <el-button text @click="openEdit(row)">编辑</el-button>
            <el-button text type="danger" @click="remove(row)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑任务' : '新增任务'" width="560px" :close-on-click-modal="false">
      <el-form :model="form" label-width="110px">
        <el-form-item label="任务名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="数据库">
          <el-select v-model="form.connection_id" style="width:100%">
            <el-option v-for="c in conns" :key="c.id" :label="connOptionLabel(c)" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="周期">
          <el-radio-group v-model="form.schedule_type">
            <el-radio value="daily">每天</el-radio>
            <el-radio value="weekly">每周</el-radio>
            <el-radio value="once">指定时间</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.schedule_type !== 'once'" label="执行时间">
          <el-time-picker v-model="timeVal" format="HH:mm" value-format="HH:mm" />
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'weekly'" label="星期">
          <el-select v-model="form.weekday" style="width:100%">
            <el-option v-for="(w, i) in weekMap" :key="i" :label="w" :value="i" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.schedule_type === 'once'" label="执行时刻">
          <el-date-picker v-model="form.once_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="备份类型">
          <el-select v-model="form.backup_type" style="width:100%">
            <el-option label="完整 Full" value="full" />
            <el-option label="差异 Differential" value="diff" />
            <el-option label="日志 Log" value="log" />
          </el-select>
          <div class="muted">差异需要已有完整备份；SIMPLE 恢复模式不能做日志备份。备份后会自动校验。</div>
        </el-form-item>
        <el-form-item label="保留天数">
          <el-input-number v-model="form.retain_days" :min="1" :max="3650" />
          <div class="muted">只留最近 N 天的日期目录。例如 2 = 今天和昨天，更早的会从 SQL Server 和群晖删除。</div>
        </el-form-item>
        <el-form-item label="压缩"><el-switch v-model="form.compress" /></el-form-item>
        <el-form-item label="删除旧备份"><el-switch v-model="form.delete_old" /></el-form-item>
        <el-form-item label="启用"><el-switch v-model="form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import http, { errMsg } from "../api";
import { connOptionLabel, fmtTime, fmtTimeShort, isAdmin, schedMap, statusText, statusType, typeMap, weekMap } from "../format";

const admin = isAdmin();
const loading = ref(false);
const saving = ref(false);
const items = ref([]);
const conns = ref([]);
const dlg = ref(false);
const form = reactive(emptyForm());

const timeVal = computed({
  get: () => form.run_time || "02:00",
  set: (v) => { form.run_time = v || "02:00"; },
});

function emptyForm() {
  return {
    id: null,
    name: "",
    connection_id: null,
    schedule_type: "daily",
    run_time: "02:00",
    weekday: 0,
    once_at: null,
    backup_type: "full",
    retain_days: 7,
    compress: true,
    delete_old: true,
    enabled: true,
  };
}

function cycleText(row) {
  if (row.schedule_type === "daily") return `每天 ${row.run_time}`;
  if (row.schedule_type === "weekly") return `每${weekMap[row.weekday] || ""} ${row.run_time}`;
  if (row.schedule_type === "once") return `一次 ${fmtTime(row.once_at)}`;
  return schedMap[row.schedule_type] || row.schedule_type;
}

async function load() {
  loading.value = true;
  try {
    const { data } = await http.get("/schedules");
    items.value = data.items || [];
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}

function openEdit(row) {
  Object.assign(form, emptyForm(), row || {});
  dlg.value = true;
}

async function save() {
  if (!form.connection_id) {
    ElMessage.warning("请选择数据库");
    return;
  }
  saving.value = true;
  try {
    const payload = { ...form };
    delete payload.id;
    delete payload.connection_name;
    delete payload.database;
    delete payload.last_status;
    delete payload.last_run_at;
    delete payload.last_error;
    delete payload.created_at;
    if (form.id) await http.put(`/schedules/${form.id}`, payload);
    else await http.post("/schedules", payload);
    ElMessage.success("已保存");
    dlg.value = false;
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    saving.value = false;
  }
}

function showError(row) {
  if (!row.last_error) return;
  ElMessageBox.alert(row.last_error, "最近错误", { confirmButtonText: "知道了" });
}

async function runNow(row) {
  try {
    await http.post(`/schedules/${row.id}/run`);
    ElMessage.success("已开始执行，进度在「数据库连接」页查看");
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function pause(row) {
  try {
    await http.post(`/schedules/${row.id}/pause`);
    ElMessage.success("已暂停");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function resume(row) {
  try {
    await http.post(`/schedules/${row.id}/resume`);
    ElMessage.success("已恢复");
    load();
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function remove(row) {
  await ElMessageBox.confirm(`删除任务「${row.name}」？`, "删除", { type: "warning" });
  try {
    await http.delete(`/schedules/${row.id}`);
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
