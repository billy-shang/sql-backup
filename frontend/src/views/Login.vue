<template>
  <div class="login-page">
    <div class="login-wrap">
      <div class="hero">
        <div class="hero-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12h3.5l2.2-6 3.6 12 2.2-6H21" />
          </svg>
        </div>
        <h1>SQL Backup</h1>
        <p>把 SQL Server 备份落到数据库服务器本地，成功后再归档群晖、通过 WebHook 通知。</p>
      </div>

      <div class="login-card">
        <h2>管理后台登录</h2>
        <p class="sub">Management Login</p>
        <el-form :model="form" label-position="top" @submit.prevent="onLogin">
          <el-form-item label="用户名">
            <el-input
              v-model="form.username"
              autocomplete="username"
              placeholder="请输入用户名"
              size="large"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入密码"
              size="large"
              @keyup.enter="onLogin"
            />
          </el-form-item>
          <el-button class="login-btn" type="primary" size="large" :loading="loading" @click="onLogin">
            登录
          </el-button>
        </el-form>
      </div>

      <div class="features">
        <div class="feat">
          <span class="dot blue" />
          <h3>本机备份</h3>
          <p>由 SQL Server 在数据库服务器上执行 BACKUP，文件不落到平台电脑。</p>
        </div>
        <div class="feat">
          <span class="dot green" />
          <h3>群晖归档</h3>
          <p>备份成功后上传 File Station，按连接名 / 库名 / 日期分目录存放。</p>
        </div>
        <div class="feat">
          <span class="dot orange" />
          <h3>WebHook 通知</h3>
          <p>支持飞书、企业微信、钉钉机器人，成功/失败及时推送值班人员。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import http, { errMsg } from "../api";

const router = useRouter();
const loading = ref(false);
const form = reactive({
  username: localStorage.getItem("sqlbackup-last-user") || "",
  password: "",
});

async function onLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning("请输入用户名和密码");
    return;
  }
  loading.value = true;
  try {
    const { data } = await http.post("/auth/login", form);
    localStorage.setItem("sqlbackup-token", data.token);
    localStorage.setItem("sqlbackup-user", JSON.stringify(data.user));
    localStorage.setItem("sqlbackup-last-user", form.username);
    ElMessage.success("登录成功");
    router.push("/");
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100%;
  background: #f5f7fa;
  display: flex;
  justify-content: center;
  padding: 48px 20px 40px;
  box-sizing: border-box;
}
.login-wrap {
  width: 100%;
  max-width: 920px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.hero {
  text-align: center;
  margin-bottom: 28px;
}
.hero-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto 16px;
  border-radius: 12px;
  background: #e8f1ff;
  color: #2f6bff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-icon svg {
  width: 26px;
  height: 26px;
}
.hero h1 {
  margin: 0;
  font-size: 32px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #1f2329;
}
.hero p {
  margin: 10px auto 0;
  max-width: 520px;
  color: #8a8f99;
  font-size: 14px;
  line-height: 1.7;
}
.login-card {
  width: 100%;
  max-width: 400px;
  background: #fff;
  border-radius: 12px;
  padding: 28px 32px 32px;
  box-shadow: 0 8px 28px rgba(31, 35, 41, 0.06);
}
.login-card h2 {
  margin: 0;
  text-align: center;
  font-size: 22px;
  font-weight: 700;
  color: #1f2329;
}
.login-card .sub {
  margin: 6px 0 22px;
  text-align: center;
  color: #a0a4ab;
  font-size: 13px;
}
.login-card :deep(.el-form-item__label) {
  color: #4e5969;
  font-weight: 500;
  margin-bottom: 4px;
}
.login-card :deep(.el-input__wrapper) {
  border-radius: 8px;
  box-shadow: 0 0 0 1px #e5e6eb inset;
  padding: 4px 12px;
}
.login-card :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #2f6bff inset;
}
.login-btn {
  width: 100%;
  margin-top: 4px;
  height: 42px;
  border-radius: 8px;
  background: #2f6bff;
  border-color: #2f6bff;
  font-size: 15px;
  font-weight: 600;
}
.login-btn:hover,
.login-btn:focus {
  background: #1f5aee;
  border-color: #1f5aee;
}
.features {
  width: 100%;
  margin-top: 36px;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
}
.feat {
  background: #fff;
  border-radius: 12px;
  padding: 18px 18px 16px;
  box-shadow: 0 6px 20px rgba(31, 35, 41, 0.04);
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-bottom: 10px;
}
.dot.blue { background: #2f6bff; }
.dot.green { background: #22c55e; }
.dot.orange { background: #f59e0b; }
.feat h3 {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 650;
  color: #1f2329;
}
.feat p {
  margin: 0;
  font-size: 13px;
  line-height: 1.65;
  color: #8a8f99;
}
@media (max-width: 720px) {
  .hero h1 { font-size: 26px; }
  .features { grid-template-columns: 1fr; }
}
</style>
