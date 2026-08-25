import { createRouter, createWebHistory } from "vue-router";
import Login from "./views/Login.vue";
import Layout from "./views/Layout.vue";
import Dashboard from "./views/Dashboard.vue";
import Connections from "./views/Connections.vue";
import Backups from "./views/Backups.vue";
import Schedules from "./views/Schedules.vue";
import Users from "./views/Users.vue";
import Settings from "./views/Settings.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: Login },
    {
      path: "/",
      component: Layout,
      children: [
        { path: "", component: Dashboard },
        { path: "connections", component: Connections },
        { path: "backups", component: Backups },
        { path: "schedules", component: Schedules },
        { path: "settings", component: Settings },
        { path: "notify", redirect: { path: "/settings", query: { tab: "notify" } } },
        { path: "users", component: Users },
      ],
    },
  ],
});

router.beforeEach((to) => {
  const token = localStorage.getItem("sqlbackup-token");
  if (to.path !== "/login" && !token) return "/login";
  if (to.path === "/login" && token) return "/";
  if (to.path === "/users") {
    try {
      const user = JSON.parse(localStorage.getItem("sqlbackup-user") || "{}");
      if (user.role !== "admin") return "/";
    } catch {
      return "/";
    }
  }
  return true;
});

export default router;
