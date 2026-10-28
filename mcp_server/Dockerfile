# MCP 서버(search_documents/search_past_answers) 컨테이너.
# sentence-transformers+torch가 들어가서 backend 이미지보다 훨씬 크고 빌드가 오래 걸림.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 참고: BGE-M3 모델 가중치는 빌드 시점에 미리 받아두지 않음 — 컨테이너가 처음 실행될 때
# (최초 검색 요청 시) 다운로드함. 다운로드한 모델은 docker-compose.yml의 hf_cache 볼륨에
# 저장되므로, 컨테이너를 새로 만들어도 다시 받지 않음.
EXPOSE 9000
ENV MCP_SERVER_HOST=0.0.0.0
CMD ["python3", "mcp_server.py"]
