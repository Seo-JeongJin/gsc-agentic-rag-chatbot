# FastAPI(AI Agent + MCP 클라이언트) 컨테이너.
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저 설치 — 코드만 바뀌고 requirements.txt가 안 바뀌면
# 이 레이어는 캐시돼서 재빌드가 빨라짐 (Docker 레이어 캐싱 활용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
