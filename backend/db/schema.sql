-- 입학 희망자 FAQ 챗봇 — DB 스키마
-- 2026-10-26 확정본
-- 참고: content_tsv는 PostgreSQL 기본 'simple' 설정 사용 — 한국어 어간추출 미지원,
--       완전 일치/토큰 단위 검색만 가능. 추후 검색품질 부족하면 pg_bigm 확장 도입 검토.

CREATE EXTENSION IF NOT EXISTS vector;


-- =========================================================
-- 1. sessions — "이 사람이 처음 왔는지, 또 왔는지"를 기억하는 테이블
-- 프론트에서 쿠키로 들고 오는 session_id를 기준으로, 세션당 정확히 1행만 존재(upsert).
-- 관리자 대시보드의 "재방문자 비율" 통계가 바로 이 테이블에서 나온다.
-- =========================================================
CREATE TABLE sessions (
  session_id   TEXT PRIMARY KEY,                 -- 프론트가 crypto.randomUUID()로 생성해 쿠키에 담아 보내는 값. 이 세션의 유일한 식별자.
  is_returning BOOLEAN NOT NULL DEFAULT FALSE,    -- 같은 session_id로 두 번째 이상 요청이 왔으면 TRUE로 바뀜. 재방문 여부를 바로 보여주는 플래그.
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),-- 이 세션이 처음 등장한 시각. 날짜별 방문자 수 통계에 사용.
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),-- 가장 최근 활동 시각. "최근 활발한 세션인지" 판단에 사용.
  conversation_reset_at TIMESTAMPTZ               -- "새 대화 시작" 버튼을 누른 시각. NULL이면 리셋한 적 없음.
                                                   -- 숏텀메모리 조회 시 이 시각 이후+24시간 이내인 것만 가져옴(memory_service.py).
);


-- =========================================================
-- 2. documents — 학과 공식 문서를 검색 가능한 조각(청크)으로 쌓아둔 테이블
-- search_documents 도구가 벡터검색+키워드검색(하이브리드)을 수행하는 대상.
-- 문서를 통째로 저장하지 않고 작은 단위로 쪼개서 저장 — 검색 정확도와 LLM에 넘길 분량을 적절히 맞추기 위함.
-- =========================================================
CREATE TABLE documents (
  id          BIGSERIAL PRIMARY KEY,
  source      TEXT NOT NULL,                     -- 이 청크가 나온 원본 문서명/식별자. 문서가 수정되면 "이 source에서 나온 청크들만" 찾아 교체하는 데 필요.
  chunk_index INTEGER NOT NULL,                   -- 같은 문서 안에서 몇 번째 조각인지. 원래 순서를 복원하거나 디버깅할 때 사용.
  chunk_text  TEXT NOT NULL,                      -- 실제 검색되고 LLM에게 보여질 텍스트 내용. 검색 결과의 본체.
  embedding   vector(1024),                       -- BGE-M3로 만든 임베딩. 벡터 유사도 검색(의미 기반)에 사용.
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', chunk_text)) STORED,
                                                   -- chunk_text로부터 DB가 자동 생성하는 키워드 검색용 컬럼. 코드가 직접 채울 필요 없음(누락 방지).
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 색인(인덱싱)된 시각. "문서 베이스가 언제 갱신됐는지" 확인용.
  UNIQUE (source, chunk_index)                    -- 같은 문서의 같은 조각이 중복으로 색인되는 걸 방지(색인 파이프라인이 실수로 두 번 돌아도 안전).
);
CREATE INDEX documents_embedding_idx ON documents USING hnsw (embedding vector_cosine_ops); -- 벡터 유사도 검색 속도용 인덱스.
CREATE INDEX documents_tsv_idx ON documents USING gin (content_tsv);                        -- 키워드 검색 속도용 인덱스.


-- =========================================================
-- 3. categories — "지금 유효한 카테고리 목록은 이거다"를 관리자가 정하는 테이블
-- 코드에 하드코딩하지 않고 DB에 저장 — 관리자가 카테고리를 바꾸면 코드 재배포 없이 즉시 반영되게 하기 위함.
-- 쓰기(INSERT/UPDATE)는 거의 안 일어나고 읽기만 자주 일어나는 테이블.
-- =========================================================
CREATE TABLE categories (
  id          SERIAL PRIMARY KEY,
  major       TEXT NOT NULL,                      -- 대분류 이름 (예: "입학전형").
  minor       TEXT NOT NULL,                       -- 소분류 이름 (예: "수시모집").
  is_approved BOOLEAN NOT NULL DEFAULT TRUE,        -- 지금 이 카테고리가 활성 상태인지. 완전히 지우는 대신 이 플래그로 비활성화 — 과거에 이 카테고리로 태깅된 질문들의 기록은 보존하면서 새로 쓰는 것만 막을 수 있음.
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (major, minor)                            -- 같은 대/소분류 조합이 실수로 중복 생성되는 것 방지.
);


-- =========================================================
-- 4. questions — 가장 많은 역할을 겸하는 핵심 테이블
-- (1) 질문/답변 로그  (2) 숏텀메모리(같은 세션 최근 대화 조회)
-- (3) 롱텀메모리(search_past_answers가 검색할 대상)  (4) 통계·대시보드 재료
-- 역할이 여러 개지만 전부 "질문 하나당 생기는 데이터"라 테이블 하나로 합쳐짐.
-- =========================================================
CREATE TABLE questions (
  id               BIGSERIAL PRIMARY KEY,
  session_id       TEXT NOT NULL REFERENCES sessions(session_id),
                                                    -- 어느 세션의 질문인지. 숏텀메모리("같은 세션의 최근 N개") 조회 시 이걸로 필터링. NOT NULL — 세션 없이 들어온 질문은 데이터 오류이므로 저장 자체를 막음.
  question_text    TEXT NOT NULL,                   -- 사용자가 실제로 물어본 원문. 로그·메모리·과거답변검색 전부 이 값이 본체.
  answer_text      TEXT,                             -- 최종적으로 사용자에게 나간 답변 원문.
  category_id      INTEGER REFERENCES categories(id),-- 이 질문이 분류된 카테고리. FK로 걸어서, 존재하지 않거나(오타) 관리되지 않는 카테고리로 잘못 태깅되는 걸 DB 차원에서 막음.
  tool_call_count  INTEGER NOT NULL DEFAULT 0,        -- 이 질문을 처리하며 Agent 루프가 MCP 도구를 호출한 횟수. 루프가 얼마나 자주 여러 번 도는지 보는 운영 지표 + 디버깅용.
  guardrail_passed BOOLEAN,                           -- 출력 가드레일(답변이 검색문서에 근거하는지) 통과 여부. NULL = 가드레일이 애초에 실행되지 않음(예: 루프 상한 도달로 조기종료), TRUE/FALSE = 실제로 실행된 결과.
  needs_review     BOOLEAN NOT NULL DEFAULT FALSE,    -- 관리자가 직접 검토해야 하는 질문인지 표시하는 플래그. 대시보드의 "답변하지 못한 질문" 위젯이 이걸로 필터링.
  review_reason    TEXT,                              -- needs_review가 TRUE인 이유. 'insufficient_info'(루프 상한까지 써도 정보 부족) 또는 'guardrail_failed'(출력 가드레일 실패로 답변이 안전문구로 교체됨).
  user_rating      SMALLINT,                           -- 사용자가 답변에 대해 남긴 평가(1~5). 평가 안 하면 NULL. 대시보드의 "사용자 평가" 위젯 재료.
  embedding        vector(1024),                       -- question_text의 임베딩. search_past_answers가 "비슷한 과거 질문"을 찾을 때 사용(롱텀메모리).
  content_tsv      tsvector GENERATED ALWAYS AS (to_tsvector('simple', question_text)) STORED,
                                                        -- question_text 기준 키워드 검색용 컬럼. DB가 자동 생성.
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 생성 시각. 숏텀메모리는 이 값으로 "최근 N개"를 정렬해 가져오고, T6 배치는 "이번 주 질문만" 걸러내는 기준으로 사용.
  -- needs_review와 review_reason이 서로 모순된 상태(필요하다는데 이유가 없거나, 필요 없다는데 이유가 있는 경우)로 저장되는 걸 막는 제약.
  CHECK (
    (needs_review = FALSE AND review_reason IS NULL) OR
    (needs_review = TRUE AND review_reason IN ('insufficient_info', 'guardrail_failed'))
  ),
  -- user_rating이 정해진 범위(1~5) 밖의 값으로 저장되는 걸 막는 제약.
  CHECK (user_rating IS NULL OR user_rating BETWEEN 1 AND 5)
);
CREATE INDEX questions_embedding_idx ON questions USING hnsw (embedding vector_cosine_ops); -- search_past_answers의 벡터검색 속도용.
CREATE INDEX questions_tsv_idx ON questions USING gin (content_tsv);                        -- search_past_answers의 키워드검색 속도용.
CREATE INDEX questions_session_idx ON questions (session_id, created_at DESC);              -- 숏텀메모리 조회("이 세션의 최근 N개") 속도용.


-- =========================================================
-- 5. category_proposals — "아직 카테고리로 확정 안 된 후보"를 담는 테이블
-- T6 주간 배치가 "기타"로 분류된 질문들을 클러스터링해서, 충분히 모이면 새 카테고리로 제안하는 결과물.
-- 시스템이 마음대로 카테고리를 만들면 안 되고 반드시 관리자 승인을 거쳐야 하므로 별도 테이블로 분리.
-- 승인되면 categories 테이블에 정식으로 행이 추가됨(한 트랜잭션으로 상태 변경까지 같이 처리).
-- =========================================================
CREATE TABLE category_proposals (
  id                     SERIAL PRIMARY KEY,
  proposed_name          TEXT NOT NULL,              -- 클러스터링으로 묶인 질문군에 붙인 제안 이름(임시).
  related_question_count INTEGER NOT NULL,           -- 이 묶음에 질문이 몇 개나 있는지. 관리자가 "승인할 만한 수요인지" 판단하는 근거.
  sample_questions       JSONB,                        -- 이 묶음에서 뽑은 질문 예시 몇 개. 관리자가 전체를 안 읽어도 묶음이 말이 되는지 빠르게 판단 가능.
  status                 TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
                                                        -- 승인 워크플로우 상태. 세 값 외에는 저장 불가하도록 제약.
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now() -- 제안된 시각. 오래된 제안 우선 처리, 중복 제안 방지 판단에 참고.
);


-- =========================================================
-- 6. admins — 관리자 계정
-- 대시보드 접근을 관리자로 제한하기 위한 최소한의 인증 정보.
-- =========================================================
CREATE TABLE admins (
  id            SERIAL PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,                 -- 로그인 아이디. 중복 가입 방지를 위해 UNIQUE.
  password_hash TEXT NOT NULL,                          -- 비밀번호를 그대로 저장하지 않고 해시(bcrypt 등)로만 저장.
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- =========================================================
-- 7. admin_sessions — 관리자 로그인 세션 (서버사이드 세션 + 쿠키 토큰 방식)
-- JWT가 아니라 이 방식을 택한 이유: FastAPI 서버가 한 대뿐이라 JWT의 "서버간 상태공유 불필요" 장점이 의미 없고,
-- 사용자용 session_id 쿠키 패턴과 구조가 같아서 일관성 있음.
-- =========================================================
CREATE TABLE admin_sessions (
  session_token TEXT PRIMARY KEY,                       -- 로그인 성공 시 발급하는 랜덤 토큰. 쿠키로 브라우저에 전달.
  admin_id      INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                                                          -- 어느 관리자의 세션인지. 관리자 계정이 삭제되면 그 세션도 같이 자동 삭제(CASCADE).
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL                     -- 세션 만료 시각(예: 발급 24시간 후). 매 요청마다 "이 시각이 지났는지"를 체크해 자동으로 로그인 무효화 — 단, 이 행 자체가 DB에서 자동 삭제되는 건 아님(필요시 별도 정리 로직 추가 가능).
);


-- =========================================================
-- 8. dashboard_insights — 관리자 대시보드용 LLM 분석 결과 캐시
-- 관리자가 "로그 분석하기" 버튼을 누를 때만 LLM을 호출해 결과를 저장하고,
-- 평소 대시보드 로딩 시에는 이 저장된 결과만 읽음 — 페이지 열 때마다 LLM을 부르지 않기 위함.
-- (주기적 자동배치가 아니라 버튼 트리거 방식 — 자원 절약 + 구현/이해 단순화)
-- =========================================================
CREATE TABLE dashboard_insights (
  id           SERIAL PRIMARY KEY,
  insight_type TEXT NOT NULL,                            -- 분석 종류 구분. 현재는 'failed_answers_summary'(답변실패 정리) 하나만 사용, 추후 종류 늘어나도 테이블 구조 그대로 재사용 가능.
  content      JSONB NOT NULL,                             -- LLM이 생성한 요약 결과. 구조가 바뀌어도 유연하게 받을 수 있도록 JSONB로.
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()           -- 이 분석이 언제 만들어졌는지. 대시보드에 "최근 분석: n일 전" 표시용.
);
