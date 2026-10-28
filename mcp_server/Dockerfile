# MCP 서버(search_documents/search_past_answers) 컨테이너.
# sentence-transformers+torch가 들어가서 backend 이미지보다 훨씬 크고 빌드가 오래 걸림.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 참고: BGE-M3 모델 가중치는 지금 빌드 시점에 미리 받아두지 않음 — 컨테이너가 처음 실행될 때
# (최초 검색 요청 시) 다운로드함. 매번 컨테이너를 새로 만들면 매번 다시 받아야 하니,
# 나중에 모델 캐시를 볼륨으로 마운트해서 재사용하는 걸 고려할 것(docker-compose.yml 참고).
EXPOSE 9000
ENV MCP_SERVER_HOST=0.0.0.0
CMD ["python3", "mcp_server.py"]
