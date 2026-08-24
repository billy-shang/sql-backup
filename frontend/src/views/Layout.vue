<template>
  <div class="app-shell">
    <el-container class="layout">
      <el-aside width="200px" class="aside">
      <div class="brand">
        <span class="brand-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 12h3.5l2.2-6 3.6 12 2.2-6H21" />
          </svg>
        </span>
        <span>SQL Backup</span>
      </div>
      <el-menu :default-active="route.path" :key="route.path" router class="aside-menu">
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item index="/connections">
          <el-icon><Connection /></el-icon>
          <span>数据库连接</span>
        </el-menu-item>
        <el-menu-item index="/backups">
          <el-icon><FolderOpened /></el-icon>
          <span>备份文件</span>
        </el-menu-item>
        <el-menu-item index="/schedules">
          <el-icon><Timer /></el-icon>
          <span>定时任务</span>
        </el-menu-item>
        <el-menu-item index="/notify">
          <el-icon><Bell /></el-icon>
          <span>通知管理</span>
        </el-menu-item>
        <el-menu-item v-if="isAdmin" index="/users">
          <el-icon><User /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
      </el-menu>
      <div class="aside-foot">
        <button type="button" class="logout-btn" @click="logout">
          <el-icon><SwitchButton /></el-icon>
          <span>退出</span>
        </button>
      </div>
      </el-aside>

      <el-container class="content-shell">
        <el-header class="header">
        <div class="title">{{ title }}</div>
        <el-dropdown
          trigger="click"
          popper-class="user-menu-popper"
          @command="onUserCommand"
          @visible-change="onUserMenuVisible"
        >
          <button ref="userBtnRef" type="button" class="user-btn">
            <span class="uname">{{ user.username || "未登录" }}</span>
            <span class="role">{{ isAdmin ? "管理员" : "普通运维" }}</span>
            <el-icon class="chev"><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="pwd">
                <el-icon><Lock /></el-icon>
                改密
              </el-dropdown-item>
              <el-dropdown-item command="help">
                <el-icon><QuestionFilled /></el-icon>
                帮助
              </el-dropdown-item>
              <el-dropdown-item command="logout" divided>
                <el-icon><SwitchButton /></el-icon>
                退出
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        </el-header>
        <el-main class="main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
    <footer class="app-footer">
      <span>SQL Backup v{{ appVersion }}</span>
      <span class="footer-separator">·</span>
      <a :href="githubUrl" target="_blank" rel="noopener noreferrer">GitHub 项目地址</a>
    </footer>
  </div>

  <el-dialog v-model="pwdVisible" title="修改密码" width="420px" :close-on-click-modal="false">
    <el-form label-width="90px">
      <el-form-item label="原密码">
        <el-input v-model="pwd.old_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="新密码">
        <el-input v-model="pwd.new_password" type="password" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="pwdVisible = false">取消</el-button>
      <el-button type="primary" :loading="pwdLoading" @click="savePwd">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="helpVisible" title="使用文档" width="680px" class="help-dlg" top="8vh">
    <div class="help-body">
      <section>
        <h3>这是做什么的</h3>
        <p>平台只下发 <code>BACKUP DATABASE</code>。<strong>备份文件写在 SQL Server 所在那台服务器</strong>，不会写到本平台电脑。登录后请尽快改密。</p>
      </section>
      <section>
        <h3>备份路径</h3>
        <p>连接里的「服务器备份目录」填数据库服务器自己的路径，例如 <code>D:\SQL_BACKUP</code>。实际文件在：</p>
        <pre>{目录}\{库名}\{YYYY-MM-DD}\{库名}_{时间}_{类型}.bak</pre>
        <p>请打开子目录查看，根目录下通常看不到 .bak。</p>
      </section>
      <section>
        <h3>数据库连接</h3>
        <ul>
          <li>直连：平台能访问 SQL 端口（通常 1433）时使用</li>
          <li>SSH：只能 SSH 时走跳板隧道，再连 SQL</li>
          <li>新增/编辑时点「测试」勾选库；系统库默认不勾选</li>
          <li>管理员可配群晖远程备份、从 .bak 恢复数据库；运维可立即备份、下载，不能改连接/任务/用户/恢复</li>
        </ul>
      </section>
      <section>
        <h3>群晖归档</h3>
        <p>在「数据库连接」→「远程备份配置」填写 File Station 地址（HTTP 5000 / HTTPS 5001）和远程目录。上传路径为：</p>
        <pre>/fileserver/DB_BackUP/{连接名}/{库名}/{日期}/文件.bak</pre>
        <p>只走 File Station，不走 22 端口。大文件会分块从 SQL Server 读取再上传。</p>
      </section>
      <section>
        <h3>恢复向导</h3>
        <ul>
          <li>在「备份文件」里点成功记录的「恢复」，或在「浏览目录」里点某个 .bak</li>
          <li>当前只支持<strong>完整备份</strong>，可恢复到原库名或新库名</li>
          <li>覆盖已有库会踢掉该库连接。文件必须在目标 SQL Server 本机看得到</li>
        </ul>
      </section>
      <section>
        <h3>定时与通知</h3>
        <ul>
          <li>完整 / 差异 / 日志备份。差异需已有完整备份；SIMPLE 库不能做日志备份。备份后会自动校验文件。</li>
          <li>保留天数：只留最近 N 天的日期目录，更早的会从 SQL Server 本机和群晖删除（需勾选「删除旧备份」）</li>
          <li>定时：每天、每周或指定时间；暂停后不再触发。容器重启后会补跑 36 小时内漏掉的任务</li>
          <li>通知：可选择飞书、企业微信、钉钉，可同时启用，填写对应机器人 Webhook</li>
        </ul>
      </section>
    </div>
    <template #footer>
      <el-button type="primary" @click="helpVisible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, reactive, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import http, { errMsg } from "../api";
import { APP_VERSION, GITHUB_URL } from "../version";

const route = useRoute();
const router = useRouter();
const appVersion = APP_VERSION;
const githubUrl = GITHUB_URL;
const user = JSON.parse(localStorage.getItem("sqlbackup-user") || '{"username":"","role":""}');
const isAdmin = computed(() => user.role === "admin");

const titles = {
  "/": "概览",
  "/connections": "数据库连接",
  "/backups": "备份文件",
  "/schedules": "定时任务",
  "/notify": "通知管理",
  "/users": "用户管理",
};
const title = computed(() => titles[route.path] || "SQL Backup");

const pwdVisible = ref(false);
const helpVisible = ref(false);
const pwdLoading = ref(false);
const pwd = reactive({ old_password: "", new_password: "" });
const userBtnRef = ref(null);
const userMenuWidth = ref(0);

function syncUserMenuWidth() {
  const w = userBtnRef.value?.offsetWidth || 0;
  if (!w) return;
  userMenuWidth.value = w;
  document.querySelectorAll(".user-menu-popper").forEach((el) => {
    el.style.width = `${w}px`;
    el.style.minWidth = `${w}px`;
  });
}

function onUserMenuVisible(visible) {
  if (visible) nextTick(syncUserMenuWidth);
}

function onUserCommand(cmd) {
  if (cmd === "pwd") openPwd();
  if (cmd === "help") helpVisible.value = true;
  if (cmd === "logout") logout();
}

function openPwd() {
  pwd.old_password = "";
  pwd.new_password = "";
  pwdVisible.value = true;
}

async function savePwd() {
  pwdLoading.value = true;
  try {
    await http.post("/auth/password", pwd);
    ElMessage.success("密码已更新");
    pwdVisible.value = false;
  } catch (e) {
    ElMessage.error(errMsg(e));
  } finally {
    pwdLoading.value = false;
  }
}

function logout() {
  localStorage.removeItem("sqlbackup-token");
  localStorage.removeItem("sqlbackup-user");
  router.push("/login");
}

onMounted(() => {
  nextTick(syncUserMenuWidth);
  window.addEventListener("resize", syncUserMenuWidth);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", syncUserMenuWidth);
});
</script>

<style scoped>
.app-shell {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.layout { flex: 1; min-height: 0; overflow: hidden; }
.content-shell { min-width: 0; }
.aside {
  display: flex;
  flex-direction: column;
  background: #0f172a;
  color: #fff;
}
.aside :deep(.el-menu) {
  border-right: 0;
  background: transparent;
}
.aside :deep(.el-menu-item) {
  color: #cbd5e1;
  height: 46px;
  margin: 4px 10px;
  border-radius: 8px;
  width: auto;
}
.aside :deep(.el-menu-item.is-active) {
  background: #2f6bff;
  color: #fff;
}
.aside :deep(.el-menu-item:hover) {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}
.aside-menu { flex: 1; overflow: auto; }
.brand {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  font-weight: 750;
  letter-spacing: 0.2px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.brand-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: #1e3a8a;
  color: #93c5fd;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.brand-icon svg { width: 16px; height: 16px; }
.aside-foot {
  padding: 10px 10px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.logout-btn {
  width: 100%;
  height: 42px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  font-size: 14px;
}
.logout-btn:hover {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
}
.header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  background: #fff;
  border-bottom: 1px solid #eef0f3;
}
.title { font-weight: 650; font-size: 16px; color: #1f2329; }
.user-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 10px 0 12px;
  border: 1px solid #eef0f3;
  border-radius: 8px;
  background: #f8f9fb;
  cursor: pointer;
  color: inherit;
}
.user-btn:hover { border-color: #dbe3ef; background: #fff; }
.uname { font-weight: 650; font-size: 14px; color: #1f2329; }
.role { font-size: 12px; color: #8a8f99; }
.chev { color: #c0c4cc; font-size: 12px; }
.main { padding: 16px 18px 24px; background: #f5f7fa; overflow-x: hidden; min-width: 0; }
.app-footer {
  flex: 0 0 34px;
  min-height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 16px;
  color: #7b8492;
  font-size: 12px;
  background: #fff;
  border-top: 1px solid #e9edf2;
  box-sizing: border-box;
}
.app-footer a {
  color: #409eff;
  text-decoration: none;
}
.app-footer a:hover { text-decoration: underline; }
.footer-separator { color: #c7ccd4; }
.help-body { max-height: 62vh; overflow: auto; padding-right: 8px; color: #4e5969; font-size: 14px; line-height: 1.7; }
.help-body h3 { margin: 0 0 8px; font-size: 15px; color: #1f2329; }
.help-body section + section { margin-top: 18px; }
.help-body p, .help-body ul { margin: 0; }
.help-body ul { padding-left: 18px; }
.help-body li { margin: 4px 0; }
.help-body code, .help-body pre {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.help-body code { padding: 1px 6px; }
.help-body pre { margin: 8px 0; padding: 10px 12px; color: #334155; white-space: pre-wrap; }
</style>

<style>
.user-menu-popper {
  min-width: 0 !important;
  box-sizing: border-box;
}
.user-menu-popper .el-dropdown-menu {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 6px 0;
}
.user-menu-popper .el-dropdown-menu__item {
  justify-content: flex-start;
}
</style>
