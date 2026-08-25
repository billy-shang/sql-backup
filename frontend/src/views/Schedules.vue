<template>
  <div class="page-card">
    <div class="page-head">
      <div>
        <h2>定时任务</h2>
        <div class="muted">每天 / 每周 / 指定时间。可立即执行一次，进度在「数据库连接」页查看。</div>
      </div>
      <div class="head-actions">
        <el-button v-if="admin" type="primary" @click="openEdit()">新增任务</el-button>
      </div>
    </div>
    <el-table :data="items" stripe v-loading="loading" class="fit-table" table-layout="fixed" empty-text="暂无定时任务">
      <el-table-column prop="name" label="任务" :min-width="narrow ? 110 : 140" show-overflow-tooltip />
      <el-table-column prop="connection_name" label="连接" :min-width="narrow ? 110 : 140" show-overflow-tooltip />
      <el-table-column v-if="!narrow" label="周期" width="132" min-width="132" show-overflow-tooltip>
        <template #default="{ row }">{{ cycleText(row) }}</template>
      </el-table-column>
      <el-table-column v-if="!narrow" label="类型" width="72" min-width="72">
        <template #default="{ row }">{{ typeMap[row.backup_type] }}</template>
      </el-table-column>
      <el-table-column v-if="!narrow" prop="retain_days" label="保留" width="72" min-width="72" />
      <el-table-column label="状态" width="100" min-width="100">
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
      <el-table-column :label="narrow ? '下次' : '下次备份时间'" :width="narrow ? 136 : 176" :min-width="narrow ? 136 : 176">
        <template #default="{ row }">{{ row.enabled ? fmtTimeShort(row.next_run_at) : "—" }}</template>
      </el-table-column>
      <el-table-column v-if="admin" label="操作" class-name="ops-col" align="left" :width="opsWidth" :min-width="opsWidth">
        <template #default="{ row }">
          <div class="ops-cell">
            <el-button text type="success" @click="runNow(row)">执行</el-button>
            <el-button v-if="!narrow && row.enabled" text @click="pause(row)">暂停</el-button>
            <el-button v-else-if="!narrow" text type="success" @click="resume(row)">恢复</el-button>
            <el-button v-if="!narrow" text @click="openEdit(row)">编辑</el-button>
            <el-button text type="danger" @click="remove(row)">删除</el-button>
            <el-dropdown v-if="narrow" trigger="click">
              <el-button text>更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item v-if="row.enabled" @click="pause(row)">暂停</el-dropdown-item>
                  <el-dropdown-item v-else @click="resume(row)">恢复</el-dropdown-item>
                  <el-dropdown-item @click="openEdit(row)">编辑</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg" :title="form.id ? '编辑任务' : '新增任务'" width="560px" :close-on-click-modal="false">
      <el-form :model="form" :label-width="narrow ? '88px' : '110px'">
        <el-form-item label="任务名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="连接">
          <el-select v-model="form.connection_id" style="width:100%" placeholder="选择数据库连接">
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
import { useBreakpoints } from "../useBreakpoints";

const admin = isAdmin();
const { narrow } = useBreakpoints();
const opsWidth = computed(() => (narrow.value ? 208 : 288));
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
    ElMessage.warning("请选择连接");
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
  await ElMessageBox.confirm(
    `确认删除定时任务「${row.name}」？只删除这条任务，不影响数据库连接和已有备份。`,
    "删除任务",
    { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" },
  );
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
