<template>
  <div class="page-card" style="max-width:720px">
    <div class="page-head">
      <div>
        <h2>飞书通知</h2>
        <div class="muted">备份成功或失败时向飞书推送卡片：成功绿色、失败红色</div>
      </div>
    </div>
    <el-form :model="form" label-width="140px" style="max-width:640px">
      <el-form-item label="启用通知">
        <el-switch v-model="form.enabled" :disabled="!admin" />
      </el-form-item>
      <el-form-item label="Webhook">
        <el-input v-model="form.feishu_webhook" :disabled="!admin" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
      </el-form-item>
      <el-form-item label="成功时通知">
        <el-switch v-model="form.notify_on_success" :disabled="!admin" />
      </el-form-item>
      <el-form-item label="失败时通知">
        <el-switch v-model="form.notify_on_fail" :disabled="!admin" />
      </el-form-item>
      <el-form-item>
        <el-button v-if="admin" type="primary" :loading="saving" @click="save">保存</el-button>
        <span v-else class="muted">普通运维仅可查看，不能修改通知配置</span>
      </el-form-item>
    </el-form>
    <el-divider />
    <div class="muted">
      成功推送绿色卡片：数据名称、数据地址、数据库、时间、备份路径（灰色正文）。<br />
      失败推送红色卡片，并附失败原因。
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import http, { errMsg } from "../api";
import { isAdmin } from "../format";

const admin = isAdmin();
const saving = ref(false);
const form = reactive({
  feishu_webhook: "",
  enabled: false,
  notify_on_success: true,
  notify_on_fail: true,
});

async function load() {
  try {
    const { data } = await http.get("/notify");
    Object.assign(form, data.item || {});
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function save() {
  saving.value = true;
  try {
    await http.put("/notify", form);
    ElMessage.success("已保存");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>
