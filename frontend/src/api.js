import axios from "axios";

const http = axios.create({ baseURL: "/api", timeout: 600000 });

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem("sqlbackup-token");
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

http.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err.response && err.response.status;
    if (status === 401) {
      localStorage.removeItem("sqlbackup-token");
      localStorage.removeItem("sqlbackup-user");
      if (!location.pathname.endsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export function errMsg(e) {
  const d = e && e.response && e.response.data;
  if (!d) return (e && e.message) || "请求失败";
  if (typeof d.detail === "string") return d.detail;
  if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg || x).join("; ");
  return d.message || "请求失败";
}

export default http;
