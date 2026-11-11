#!/usr/bin/env bash
# 배포 서버 업데이트 스크립트 — 코드를 고쳐 GitHub에 push한 뒤, 서버(aihub.yju.ac.kr)에서 이것 하나만 실행 (2026-11-11 배포 0-8).
#
# 하는 일: ① 사전 확인 → ② 최신 코드 받기 → ③ 다시 빌드·교체 → ④ 자동 확인 → ⑤ 놓치기 쉬운 추가 작업 안내
# 왜 스크립트로 두나: 매번 손으로 치면 순서를 빼먹기 쉬움(특히 pull만 하고 빌드를 안 하면 위젯 파일이 안 바뀜 — 위젯은 빌드 때 만들어짐).
# 확인할 때 실제 질문을 보내지 않는 이유: 질문하면 DB에 기록이 남아 대시보드 통계에 가짜 질문이 섞임 → vLLM 접속만 따로 확인.
#
# 사용법 (프로젝트 폴더 어디서든):  ./deploy/deploy.sh
# 처음 배포는 이 스크립트가 아니라 deploy/README.md 순서대로(.env 작성·문서 색인·관리자 계정·https 첫 발급이 필요해서).
set -euo pipefail   # 명령 하나라도 실패하면 바로 멈춤 — 반쯤 바뀐 상태로 넘어가지 않게

cd "$(dirname "$0")/.."   # 어디서 실행하든 프로젝트 폴더 기준으로
dc() { docker compose --profile deploy "$@"; }   # 배포용 프로필(nginx·certbot 포함)
ok()   { echo "  ✅ $*"; }
fail() { echo "  ❌ $*"; FAILED=1; }
FAILED=0

echo "[1/5] 사전 확인"
# .env가 없으면 컨테이너가 안 뜸(비밀번호·모드가 필수 값) — 처음 배포라면 README부터
if [ ! -f .env ]; then
  echo "  중단: .env가 없음 — 처음 배포는 deploy/README.md 순서대로"
  exit 1
fi
# 관리자 영역 허용 IP 파일(git 제외)이 없으면 견본에서 만듦 — 없는 채로 띄우면 Docker가 빈 폴더를 만들어 nginx가 못 켜짐
if [ ! -f deploy/nginx/admin-allow.conf ]; then
  cp deploy/nginx/admin-allow.conf.example deploy/nginx/admin-allow.conf
  echo "  ⚠️ admin-allow.conf가 없어서 견본(모두 허용)으로 만듦 — 학교 IP를 채울 것(README 7단계)"
fi
# 서버에서 git으로 관리되는 파일을 직접 고쳐 둔 게 있으면 멈춤 — pull이 그걸 덮어쓰거나 충돌로 멈추지 않게.
# (.env·admin-allow.conf처럼 git 제외 파일은 여기 안 걸림) 고친 게 꼭 필요하면 학과 PC에서 커밋해 push한 뒤 다시 실행.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "  중단: 서버에서 수정된 파일이 있음 — 아래 파일을 확인하고, 필요 없으면 git checkout -- <파일>로 되돌린 뒤 다시 실행"
  git status --short --untracked-files=no
  exit 1
fi
ok "사전 확인 통과"

echo "[2/5] 최신 코드 받기"
BEFORE="$(git rev-parse HEAD)"
git pull --ff-only   # --ff-only: 서버 기록과 GitHub 기록이 갈라졌으면 합치지 말고 멈춤(서버에서 몰래 커밋한 경우 등)
AFTER="$(git rev-parse HEAD)"
if [ "$BEFORE" = "$AFTER" ]; then
  echo "  새 커밋 없음 — 그래도 다시 빌드·확인은 진행"
else
  echo "  $(git log --oneline "$BEFORE..$AFTER" | wc -l)개 커밋 받음:"
  git log --oneline "$BEFORE..$AFTER" | sed 's/^/    /'
fi
CHANGED="$(git diff --name-only "$BEFORE" "$AFTER")"

echo "[3/5] 다시 빌드·교체 (바뀐 것만 새로 만듦, 몇 분 걸릴 수 있음)"
dc up -d --build

echo "[4/5] 자동 확인"
# 챗봇이 뜰 때까지 최대 90초 기다림(MCP 서버 연결 등으로 몇 초 걸림)
for _ in $(seq 1 45); do
  curl -fsS -o /dev/null http://127.0.0.1:8080/widget.js 2>/dev/null && break
  sleep 2
done
for svc in db mcp_server backend nginx certbot; do
  if dc ps --status running --services 2>/dev/null | grep -qx "$svc"; then ok "$svc 실행 중"; else fail "$svc 안 떠 있음 — docker compose --profile deploy logs $svc"; fi
done
if dc exec -T nginx nginx -t >/dev/null 2>&1; then ok "nginx 설정 문법"; else fail "nginx 설정 오류 — docker compose --profile deploy exec nginx nginx -t"; fi
if curl -fsS -o /dev/null http://127.0.0.1:8080/widget.js; then ok "위젯 파일(/widget.js)"; else fail "위젯 파일이 안 나옴 — docker compose --profile deploy logs backend"; fi
# vLLM 접속 확인 — 컨테이너 안에서 .env에 적힌 주소로 모델 목록만 요청(질문은 안 보냄). mock 모드면 건너뜀
VLLM_CHECK='import os, sys, urllib.request
mode, url = os.environ.get(sys.argv[1], ""), os.environ.get(sys.argv[2], "").rstrip("/")
if mode != "vllm":
    print("skip:" + mode); sys.exit(0)
urllib.request.urlopen(url + "/v1/models", timeout=10).read()
print("ok:" + url)'
for pair in "backend LLM_MODE VLLM_URL 생성" "mcp_server EMBEDDING_MODE EMBEDDING_URL 임베딩"; do
  set -- $pair
  if out="$(dc exec -T "$1" python3 -c "$VLLM_CHECK" "$2" "$3" 2>&1)"; then
    case "$out" in
      skip:*) echo "  ⚠️ $4 모델: $2=${out#skip:} — 운영 서버라면 vllm이어야 함" ;;
      *)      ok "$4 모델(vLLM) 접속 ${out#ok:}" ;;
    esac
  else
    fail "$4 모델(vLLM) 접속 실패 — .env의 $3 확인(README '문제가 생기면' 참고)"
  fi
done

echo "[5/5] 추가로 챙길 것"
NOTE=0
# 문서 자르는 방식이 바뀌면 DB의 문서 조각이 옛 방식 그대로라 다시 색인해야 같은 상태가 됨
if echo "$CHANGED" | grep -qE '^mcp_server/(chunking|indexing_pipeline|embedding)\.py$'; then
  echo "  📌 문서 색인 방식이 바뀜 → 다시 색인:  docker compose exec mcp_server python3 index_cli.py /data/<FAQ파일>.md"; NOTE=1
fi
# schema.sql은 DB가 비어 있을 때만 자동 실행됨 — 이미 있는 DB엔 바뀐 부분을 직접 적용해야 함
if echo "$CHANGED" | grep -q '^backend/db/schema.sql$'; then
  echo "  📌 DB 구조(schema.sql)가 바뀜 → 자동 반영 안 됨. 바뀐 부분을 SQL로 직접 적용할 것(git diff $BEFORE $AFTER -- backend/db/schema.sql)"; NOTE=1
fi
# .env.example에 새 설정이 생겼는데 서버 .env엔 없으면 알려줌(값은 안 보여줌 — 이름만)
MISSING="$(comm -23 <(grep -oE '^[A-Z_]+=' .env.example | sort -u) <(grep -oE '^[A-Z_]+=' .env | sort -u) | tr -d '=' | tr '\n' ' ')"
if [ -n "$MISSING" ]; then
  echo "  📌 .env에 없는 설정: $MISSING— .env.example 설명을 보고 필요하면 추가"; NOTE=1
fi
[ "$NOTE" = 0 ] && echo "  없음"

echo
if [ "$FAILED" = 0 ]; then
  echo "✅ 배포 완료 ($(git log -1 --format='%h %s'))"
else
  echo "❌ 확인 실패 항목이 있음 — 위 ❌ 줄 확인. 이전 버전으로 되돌리기:"
  echo "   git checkout $BEFORE && docker compose --profile deploy up -d --build   (고친 뒤엔 git checkout main)"
  exit 1
fi
