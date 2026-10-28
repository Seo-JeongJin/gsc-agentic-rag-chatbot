// 백엔드(admin_api.py) 전용 fetch 래퍼. credentials:"include"가 핵심 —
// 이게 있어야 로그인 시 받은 HttpOnly 세션쿠키가 이후 모든 요청에 자동으로 실림.
const BASE = "/admin";

export class UnauthorizedError extends Error {}

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) throw new UnauthorizedError();
  if (!res.ok) throw new Error(`요청 실패: ${res.status}`);
  if (res.status === 204) return null;
  return res.json();
}

export const getMe = () => request("/me");
export const login = (username, password) =>
  request("/login", { method: "POST", body: JSON.stringify({ username, password }) });
export const logout = () => request("/logout", { method: "POST" });

export const getOverview = () => request("/stats/overview");
export const getCategoryStats = () => request("/stats/categories");

export const getQuestions = (params = {}) => {
  const qs = new URLSearchParams(params).toString();
  return request(`/questions${qs ? "?" + qs : ""}`);
};

export const analyze = () => request("/analyze", { method: "POST" });
export const getLatestInsight = () => request("/insights");
export const getInsightHistory = (limit = 20) => request(`/insights/history?limit=${limit}`);

export const analyzeCategorySuggestions = () =>
  request("/category-suggestions/analyze", { method: "POST" });
export const getCategorySuggestions = () => request("/category-suggestions");
export const approveCategorySuggestion = (id) =>
  request(`/category-suggestions/${id}/approve`, { method: "POST" });
export const rejectCategorySuggestion = (id) =>
  request(`/category-suggestions/${id}/reject`, { method: "POST" });
