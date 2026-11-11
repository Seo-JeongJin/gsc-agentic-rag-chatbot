#!/usr/bin/env bash
# https 인증서 첫 발급 스크립트 — 배포 서버(GPU 서버, aihub.yju.ac.kr)에서 한 번만 실행 (2026-11-11 배포 0-2).
#
# 왜 스크립트로 두나: 첫 발급은 "닭과 달걀" 순서라 손으로 하면 틀리기 쉬움 —
#   https 설정으로 nginx를 켜려면 인증서가 있어야 하고, 인증서를 받으려면 nginx가 먼저 80번에서 떠 있어야 함.
#   그래서 ① http 설정으로 nginx를 켜고 → ② certbot으로 인증서를 받고 → ③ (사람이) https 설정으로 바꾸는 순서.
# 왜 연습(--staging)부터: Let's Encrypt 진짜 서버는 같은 주소로 실패를 반복하면 한동안 발급을 막음.
#   연습 서버로 먼저 "외부에서 80번 확인이 되는지"를 성공시킨 뒤 진짜 발급을 함.
#
# 사용법 (프로젝트 폴더에서):
#   ./deploy/init-https.sh <이메일> --staging   # 1) 연습 발급 — 브라우저가 믿지 않는 가짜 인증서, 확인용
#   ./deploy/init-https.sh <이메일>             # 2) 진짜 발급
# 이메일: 인증서 만료 경고를 받을 주소(Let's Encrypt에 등록됨).
set -euo pipefail   # 명령 하나라도 실패하면 바로 멈춤 — 반쯤 진행된 상태로 넘어가지 않게

cd "$(dirname "$0")/.."   # 어디서 실행하든 프로젝트 폴더 기준으로
DOMAIN="aihub.yju.ac.kr"
EMAIL="${1:-}"
MODE="${2:-}"

if [ -z "$EMAIL" ] || { [ -n "$MODE" ] && [ "$MODE" != "--staging" ]; }; then
  echo "사용법: $0 <이메일> [--staging]"
  exit 1
fi

# docker compose 명령을 짧게 — 배포용 프로필(nginx·certbot 포함)로 실행
dc() { docker compose --profile deploy "$@"; }

# ① 지금 https 설정이면 멈춤 — 인증서가 없는 상태에서 https로 켜면 nginx가 죽어서 확인 파일을 못 보여줌
if grep -qE '^NGINX_CONF=nginx\.https\.conf' .env 2>/dev/null; then
  echo "중단: .env가 NGINX_CONF=nginx.https.conf로 되어 있음. 첫 발급은 http 설정에서 해야 함 — 그 줄을 지우고 다시 실행."
  exit 1
fi

# ② 진짜 발급인데 이미 진짜 인증서가 있으면 멈춤 — 다시 받을 필요가 없고, 반복 발급은 Let's Encrypt가 제한함
#    (연습 인증서만 있으면 진짜로 덮어써야 하므로 계속 진행)
EXISTING_SERVER="$(dc run --rm --no-deps --entrypoint sh certbot -c \
  "grep '^server' /etc/letsencrypt/renewal/${DOMAIN}.conf 2>/dev/null || true")"
if [ -z "$MODE" ] && [ -n "$EXISTING_SERVER" ] && ! echo "$EXISTING_SERVER" | grep -q staging; then
  echo "중단: 이미 진짜 인증서가 있음(${EXISTING_SERVER}). 갱신은 certbot 컨테이너가 자동으로 함."
  exit 1
fi

# 관리자 영역 허용 IP 파일(git 제외, 배포 0-8)이 없으면 견본(모두 허용)에서 만듦 — 없는 채로 nginx를 띄우면
# Docker가 같은 이름의 빈 폴더를 만들어 nginx가 못 켜짐. 학교 IP는 https까지 끝낸 뒤 채움(deploy/README.md)
[ -f deploy/nginx/admin-allow.conf ] || cp deploy/nginx/admin-allow.conf.example deploy/nginx/admin-allow.conf

# ③ nginx를 http 설정으로 켬(이미 떠 있으면 그대로) — Let's Encrypt 확인 파일을 80번에서 보여줄 수 있게
echo "[1/3] nginx(http) 실행"
dc up -d nginx
sleep 3

# ④ 확인 경로가 nginx에서 실제로 보이는지 먼저 시험 — 여기서 실패하면 Let's Encrypt도 실패하므로 발급 시도 전에 멈춤
echo "[2/3] 확인 경로 시험"
dc run --rm --no-deps --entrypoint sh certbot -c \
  'mkdir -p /var/www/certbot/.well-known/acme-challenge && echo ok > /var/www/certbot/.well-known/acme-challenge/probe'
if ! curl -fsS -H "Host: ${DOMAIN}" http://127.0.0.1/.well-known/acme-challenge/probe | grep -q ok; then
  echo "중단: nginx가 확인 경로를 못 보여줌 — docker compose logs nginx 확인"
  exit 1
fi
dc run --rm --no-deps --entrypoint sh certbot -c 'rm -f /var/www/certbot/.well-known/acme-challenge/probe'

# ⑤ 발급 — 연습이면 --staging. 진짜 발급은 --force-renewal: 연습 인증서가 남아 있으면 certbot이 "아직 갱신할 때가 아님"으로 넘어가 버려서
#    진짜로 덮어쓰게 함(②에서 진짜 인증서가 이미 있는 경우는 막았으므로 불필요한 재발급은 안 일어남)
echo "[3/3] 인증서 발급 (${MODE:-진짜})"
EXTRA="--force-renewal"
[ "$MODE" = "--staging" ] && EXTRA="--staging"
dc run --rm --no-deps --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot -d "$DOMAIN" \
  --email "$EMAIL" --agree-tos --no-eff-email --non-interactive $EXTRA

echo
if [ "$MODE" = "--staging" ]; then
  echo "✅ 연습 발급 성공 — 외부에서 80번 확인이 됨. 이제 진짜 발급:  $0 $EMAIL"
else
  echo "✅ 진짜 인증서 발급 완료. 다음을 순서대로:"
  echo "   1) .env에 한 줄 추가:            NGINX_CONF=nginx.https.conf"
  echo "   2) https로 다시 켜고 갱신 시작:   docker compose --profile deploy up -d nginx certbot"
  echo "   3) 확인: 브라우저에서 https://${DOMAIN}/ (자물쇠), http://${DOMAIN}/ 가 https로 넘어가는지"
  echo "   되돌리기: .env의 그 줄을 지우고 docker compose --profile deploy up -d nginx"
fi
