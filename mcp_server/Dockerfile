# MCP 서버(search_documents/search_past_answers + 내부 임베딩 API) 컨테이너.
FROM python:3.12-slim

WORKDIR /app

# torch를 "CPU 전용" 버전으로 먼저 설치 (2026-10-28).
# 그냥 pip install하면 Linux에선 기본으로 GPU(CUDA)용 torch가 깔려서 NVIDIA 라이브러리(nvidia/triton 약 4GB)가
# 같이 들어왔음 — 이 컨테이너는 GPU를 쓰지 않으므로(로컬 임베딩은 CPU, 학교에선 GPU 서버의 vLLM을 HTTP로 호출)
# 쓸모없는 용량이었음(이미지 9.7GB의 주원인). torch를 먼저 깔아두면 아래 sentence-transformers 설치 때
# "이미 설치됨"으로 보고 GPU 버전을 따로 받지 않음.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 참고: BGE-M3 모델 가중치는 빌드 시점에 미리 받아두지 않음 — EMBEDDING_MODE=local일 때 첫 임베딩 요청 시
# 다운로드하고, docker-compose.yml의 hf_cache 볼륨에 저장되므로 컨테이너를 새로 만들어도 다시 받지 않음.
# EMBEDDING_MODE=vllm(학교)이면 모델을 아예 받지 않음(계산은 GPU 서버가 함).
EXPOSE 9000
ENV MCP_SERVER_HOST=0.0.0.0
CMD ["python3", "mcp_server.py"]
