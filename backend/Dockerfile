# FastAPI(AI Agent + MCP 클라이언트 + 관리자 대시보드 서빙) 컨테이너.
#
# 2단계 빌드(2026-10-28, 리뷰 L7): ① Node 이미지에서 React 대시보드를 빌드 → ② Python 이미지에는 빌드 결과물만 복사.
# 이유: 대시보드 빌드 결과물(backend/static_dashboard/)은 git에 안 올리기 때문에, 전엔 새로 clone한 컴퓨터에서
# 직접 npm으로 빌드해야만 화면이 나왔음(그 컴퓨터에 Node.js 필요). 이제는 docker compose up --build만 하면
# 대시보드까지 자동으로 들어감. Node는 ①단계에만 있고 최종 이미지엔 안 남음(용량 증가 없음).
#
# 빌드할 때 보는 폴더(build context)가 프로젝트 루트임 — frontend/dashboard와 backend 둘 다 필요해서.
# 그래서 경로가 전부 "backend/…", "frontend/…"로 시작하고, 루트의 .dockerignore가 .env 등을 제외함.

# ---------- ① 대시보드 빌드 ----------
FROM node:20-alpine AS dashboard

WORKDIR /build/frontend/dashboard
# package*.json만 먼저 복사해서 의존성 설치 — 소스만 바뀌면 이 레이어는 캐시돼서 재빌드가 빠름
COPY frontend/dashboard/package.json frontend/dashboard/package-lock.json ./
RUN npm ci
COPY frontend/dashboard/ ./
# vite.config.js의 outDir이 "../../backend/static_dashboard"라서 결과물은 /build/backend/static_dashboard에 생김
RUN npm run build

# ---------- ①-2 위젯 빌드 (2026-10-28) ----------
# 위젯 번들(widget.js)도 git에 안 올리므로 여기서 만들어서, backend가 /widget-test/에서 테스트 페이지와 함께 내려줌
# → 학과 컴퓨터에서 Node.js 없이 위젯 화면을 확인 가능.
FROM node:20-alpine AS widget

WORKDIR /build/frontend/widget
COPY frontend/widget/package.json frontend/widget/package-lock.json ./
RUN npm ci
COPY frontend/widget/ ./
RUN npm run build   # esbuild → dist/widget.js

# ---------- ② 실행 이미지 ----------
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 — 코드만 바뀌고 requirements.txt가 안 바뀌면 이 레이어는 캐시됨
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=dashboard /build/backend/static_dashboard ./static_dashboard
# 위젯 테스트 페이지: widget-test.html을 index.html로(→ /widget-test/ 주소로 바로 열림) + 번들
COPY --from=widget /build/frontend/widget/dist/widget.js ./static_widget/widget.js
COPY --from=widget /build/frontend/widget/widget-test.html ./static_widget/index.html

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
