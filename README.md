# GSC Agentic RAG Chatbot

영진전문대학교 글로벌시스템융합과 입학 희망자를 위한 **Agentic RAG FAQ 챗봇**과 **관리자 대시보드**입니다. (캡스톤 프로젝트)

- 학과 홈페이지에 넣는 채팅 위젯으로 입학 관련 질문에 답변합니다.
- LLM이 매 턴마다 "검색이 더 필요한지"를 스스로 판단하고(tool-calling 루프), 검색은 MCP 서버가 담당합니다.
- 관리자는 대시보드에서 질문 로그, 카테고리 통계, 답변하지 못한 질문의 요약, 새 카테고리 제안을 확인합니다.

설계 원칙은 **"판단은 LLM, 실행·제약·중계는 코드"**입니다. 도구 호출 여부와 답변 내용은 LLM이 정하고, 네트워크 호출, 재시도, 반복 상한, 입력/출력 가드레일, DB 접근은 코드가 담당합니다.

## 구조

```
브라우저(위젯) ──HTTP──▶ backend (FastAPI :8080) ──MCP(HTTP)──▶ mcp_server (FastMCP :9000) ──▶ PostgreSQL + pgvector
                           │  입력 가드레일 → Agent 루프 → 출력 가드레일 → 로그        │  하이브리드 검색(벡터 + 키워드, RRF)
                           │                                                           │
                           └──────── HTTP(OpenAI 호환) ──▶ vLLM (생성 LLM, GPU 서버)    └──▶ vLLM (임베딩 BGE-M3, GPU 서버)
관리자 브라우저 ──HTTP──▶ backend가 React 대시보드도 같은 주소에서 서빙(/)
```

| 구성요소 | 기술 |
|---|---|
| 생성 LLM | Qwen3-14B-AWQ on vLLM (OpenAI 호환 API) |
| 임베딩 | BGE-M3 (1024차원) — vLLM 또는 로컬 CPU |
| 벡터 DB | PostgreSQL 16 + pgvector (HNSW) + tsvector 키워드 검색 |
| 백엔드 | FastAPI, FastMCP (MCP 서버/클라이언트) |
| 프론트엔드 | 위젯: 바닐라 JS + Shadow DOM / 대시보드: React + Vite |
| 실행 | Docker Compose (db, mcp_server, backend) |

```
backend/         FastAPI — main.py(라우트), agent_loop.py(Agent 루프), llm_client.py(LLM 호출 입구),
                 mcp_client.py, guardrail.py(입력), output_guardrail.py(출력), system_prompt.py, admin_api.py
backend/db/      DB 접근 — schema.sql, seed_categories.sql, 세션/메모리/로그/관리자/대시보드/분석/카테고리
mcp_server/      FastMCP 서버 — 검색 도구 2개, 임베딩(embedding.py), 청킹, 색인(index_cli.py)
frontend/widget/     채팅 위젯 (esbuild 번들)
frontend/dashboard/  관리자 대시보드 (React, Docker 빌드 시 자동으로 함께 빌드됨)
```

## 실행 모드

`.env`의 두 값으로 LLM과 임베딩을 어디서 돌릴지 정합니다. 코드는 바꾸지 않습니다.

| 설정 | 집 (GPU 서버 접속 불가) | 학교 (학과망) |
|---|---|---|
| `LLM_MODE` | `mock` — 가짜 응답 | `vllm` — GPU 서버의 생성 모델 |
| `EMBEDDING_MODE` | `local` — Docker 안에서 CPU로 BGE-M3 | `vllm` — GPU 서버의 임베딩 모델 |

## 빠른 시작 (로컬)

사전 준비: Docker(Docker Desktop 또는 Docker Engine + Compose), git

```bash
git clone https://github.com/Seo-JeongJin/gsc-agentic-rag-chatbot.git
cd gsc-agentic-rag-chatbot

cp .env.example .env            # POSTGRES_PASSWORD를 원하는 값으로 바꾸기 (기본 모드: mock + local)
docker compose up -d --build    # db → mcp_server → backend 순서로 준비될 때까지 기다리며 뜸
```

- 챗봇 API: `http://localhost:8080/api/ask`
- 관리자 대시보드: `http://localhost:8080/`
- DB를 처음 만들 때 카테고리 31개가 자동으로 들어갑니다(`backend/db/seed_categories.sql`).

### 문서 색인 (검색 대상 넣기)

학과 문서(마크다운)를 프로젝트 루트의 `data/` 폴더에 넣고 색인합니다. `data/`는 git에 올라가지 않습니다.

```bash
mkdir -p data
# data/ 에 문서 파일을 넣은 뒤
docker compose exec mcp_server python3 index_cli.py /data/<문서파일>.md
```

같은 파일을 다시 색인하면 그 문서의 기존 청크가 지워지고 새로 들어갑니다. 문서를 수정하거나 임베딩 모드를 바꾼 뒤에 다시 실행하세요.

### 관리자 계정 만들기

```bash
docker compose exec backend python3 create_admin_cli.py <아이디> <비밀번호>
```

### 질문 보내 보기

```bash
curl -X POST http://localhost:8080/api/ask -H "Content-Type: application/json" \
  -d '{"question_text": "기숙사 신청은 어떻게 하나요?", "session_id": "test-1"}'
```

## 학과 컴퓨터에서 실측하기 (vLLM 연결)

GPU 서버는 학과망에서만 접속할 수 있으므로, **학과망에 연결된 컴퓨터**에서 진행합니다.

1. **사전 확인**: 그 컴퓨터에 Docker와 git이 설치돼 있는지 확인합니다. Node.js는 필요 없습니다(대시보드는 Docker 빌드 때 자동 빌드).
2. **GPU 서버 확인**: 생성 모델과 임베딩 모델의 vLLM이 떠 있는지, 실제 모델 이름이 무엇인지 확인합니다.
   ```bash
   curl http://<GPU서버 주소>:8000/v1/models   # 생성 모델 이름 → VLLM_MODEL
   curl http://<GPU서버 주소>:8001/v1/models   # 임베딩 모델 이름 → EMBEDDING_MODEL
   ```
3. **코드 받기 + `.env` 작성**
   ```bash
   git clone https://github.com/Seo-JeongJin/gsc-agentic-rag-chatbot.git
   cd gsc-agentic-rag-chatbot
   cp .env.example .env
   ```
   `.env`를 열어 아래 값을 채웁니다.
   ```
   POSTGRES_PASSWORD=<원하는 비밀번호>
   LLM_MODE=vllm
   VLLM_URL=http://<GPU서버 주소>:8000
   VLLM_MODEL=<2번에서 확인한 생성 모델 이름>
   EMBEDDING_MODE=vllm
   EMBEDDING_URL=http://<GPU서버 주소>:8001
   EMBEDDING_MODEL=<2번에서 확인한 임베딩 모델 이름>
   ```
4. **실행**: `docker compose up -d --build`
5. **문서 색인**: 받은 FAQ 문서를 `data/`에 넣고 `docker compose exec mcp_server python3 index_cli.py /data/<문서파일>.md`
6. **관리자 계정**: `docker compose exec backend python3 create_admin_cli.py <아이디> <비밀번호>`
7. **확인**: 위의 curl로 질문을 보내고, `http://<이 컴퓨터>:8080/`에서 대시보드에 로그인해 질문 로그를 확인합니다.

## 테스트

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # 최초 1회
.venv/bin/pytest                                      # backend: 단위 + 통합
cd ../mcp_server && ../backend/.venv/bin/pip install -r requirements.txt && ../backend/.venv/bin/pytest
```

- 단위 테스트는 DB나 서버 없이 돌아갑니다.
- 통합 테스트는 실행 중인 DB 컨테이너(`docker compose up -d`)에 **별도 테스트 DB**(`rag_test`, `rag_test_mcp`)를 만들어 사용합니다. 개발 DB는 건드리지 않고, DB에 접속할 수 없으면 자동으로 건너뜁니다.
- 테스트는 항상 가짜 LLM을 씁니다(`.env`가 vllm이어도 GPU 서버를 부르지 않음).

## 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| `required variable POSTGRES_USER is missing` | `.env`가 없습니다 → `cp .env.example .env` |
| `/api/ask`가 503 | LLM 서버 응답 없음 → `VLLM_URL`, GPU 서버 상태, `docker compose logs backend` |
| 관리자 "로그 분석하기"가 502 | 위와 같음(LLM 서버 문제) |
| 검색 결과가 비어 있음 | 문서를 색인했는지 확인 → `index_cli.py` |
| 컨테이너 상태 확인 | `docker compose ps`, `docker compose logs -f <서비스명>` |
