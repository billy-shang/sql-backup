<template>
  <div class="page-card" style="max-width:820px">
    <div class="page-head">
      <div>
        <h2>通知管理</h2>
        <div class="muted">备份完成后，可向飞书、企业微信、钉钉推送结果，可单选或同时启用</div>
      </div>
    </div>
    <el-form :model="form" label-width="140px" style="max-width:740px">
      <el-form-item label="启用通知">
        <el-switch v-model="form.enabled" :disabled="!admin" />
      </el-form-item>
      <el-form-item label="通知渠道">
        <el-checkbox-group v-model="channels" :disabled="!admin || !form.enabled">
          <el-checkbox value="feishu">飞书通知</el-checkbox>
          <el-checkbox value="wecom">企业微信通知</el-checkbox>
          <el-checkbox value="dingtalk">钉钉通知</el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="飞书 Webhook">
        <el-input
          v-model="form.feishu_webhook"
          :disabled="!admin || !form.enabled || !channels.includes('feishu')"
          placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
        />
      </el-form-item>
      <el-form-item label="企微 Webhook">
        <el-input
          v-model="form.wecom_webhook"
          :disabled="!admin || !form.enabled || !channels.includes('wecom')"
          placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
        />
      </el-form-item>
      <el-form-item label="钉钉 Webhook">
        <el-input
          v-model="form.dingtalk_webhook"
          :disabled="!admin || !form.enabled || !channels.includes('dingtalk')"
          placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
        />
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
      飞书使用交互式卡片，企业微信和钉钉使用 Markdown 消息。成功显示成功状态，失败附带失败原因。
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
const channels = ref(["feishu"]);
const form = reactive({
  feishu_webhook: "",
  wecom_webhook: "",
  dingtalk_webhook: "",
  enabled: false,
  notify_on_success: true,
  notify_on_fail: true,
});

function parseChannels(raw) {
  const value = String(raw || "feishu").trim();
  if (value === "both") return ["feishu", "wecom"];
  if (value === "all") return ["feishu", "wecom", "dingtalk"];
  const allowed = new Set(["feishu", "wecom", "dingtalk"]);
  const items = value.split(",").map((s) => s.trim()).filter((s) => allowed.has(s));
  return items.length ? items : ["feishu"];
}

async function load() {
  try {
    const { data } = await http.get("/notify");
    Object.assign(form, data.item || {});
    channels.value = parseChannels(data.item?.notify_channel);
  } catch (e) {
    ElMessage.error(errMsg(e));
  }
}

async function save() {
  if (form.enabled && !channels.value.length) {
    ElMessage.warning("请至少选择一个通知渠道");
    return;
  }
  if (form.enabled && channels.value.includes("feishu") && !form.feishu_webhook.trim()) {
    ElMessage.warning("请填写飞书 Webhook");
    return;
  }
  if (form.enabled && channels.value.includes("wecom") && !form.wecom_webhook.trim()) {
    ElMessage.warning("请填写企业微信 Webhook");
    return;
  }
  if (form.enabled && channels.value.includes("dingtalk") && !form.dingtalk_webhook.trim()) {
    ElMessage.warning("请填写钉钉 Webhook");
    return;
  }
  saving.value = true;
  try {
    const notify_channel = channels.value.join(",") || "feishu";
    await http.put("/notify", { ...form, notify_channel });
    ElMessage.success("已保存");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    saving.value = false;
  }
}

onMounted(load);
</script>
