export const typeMap = { full: "完整", diff: "差异", log: "日志" };
export const modeMap = { direct: "直连", ssh: "SSH 代理" };
export const schedMap = { daily: "每天", weekly: "每周", once: "指定时间" };
export const weekMap = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

export function statusText(s) {
  return { success: "成功", failed: "失败", running: "运行中", paused: "暂停" }[s] || s || "-";
}

export function statusType(s) {
  return { success: "success", failed: "danger", running: "warning", paused: "info" }[s] || "";
}

export function fmtTime(v) {
  if (!v) return "-";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v).replace("T", " ").slice(0, 19);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtSize(n) {
  const x = Number(n) || 0;
  if (x <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = x;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)}${units[i]}`;
}

export function dbLabel(raw) {
  const names = String(raw || "")
    .replace(/;/g, ",")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return names.length ? names.join("、") : "全部用户数据库";
}

export function isAdmin() {
  try {
    return JSON.parse(localStorage.getItem("sqlbackup-user") || "{}").role === "admin";
  } catch {
    return false;
  }
}
