import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 중엔 Vite가 5173번에서 뜨지만, /admin으로 가는 요청은 전부 백엔드(8080)로 그대로
// 전달(proxy)해서 브라우저가 "같은 출처"로 착각하게 만듦 — 그래야 로그인 쿠키(HttpOnly)가
// 정상적으로 실림. 운영 빌드는 결과물을 backend/static_dashboard로 내보내서 FastAPI가
// 같은 서버에서 직접 서빙(= 진짜로 같은 출처가 됨, proxy 흉내가 필요 없어짐).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/admin": "http://localhost:8080",
    },
  },
  build: {
    outDir: "../../backend/static_dashboard",
    emptyOutDir: true,
  },
});
