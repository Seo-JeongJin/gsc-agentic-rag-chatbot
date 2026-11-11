# 배포 안내 — aihub.yju.ac.kr

챗봇을 GPU 서버(`aihub.yju.ac.kr`)에 올리는 순서. **처음 배포는 1~9단계를 순서대로**, 그 뒤 코드가 바뀌면 [10. 업데이트](#10-업데이트-코드가-바뀐-뒤)만.

```
지원자 ──https──▶ aihub.yju.ac.kr (학교 방화벽: 80·443만 열림)
                      │
              [GPU 서버]
                ├ nginx      80·443 → 챗봇. https 인증서, 관리자 영역 IP 제한
                ├ certbot    인증서 자동 갱신
                ├ backend    챗봇 (127.0.0.1:8080 — 외부에서 직접 접근 불가)
                ├ mcp_server 문서 검색
                ├ db         PostgreSQL + pgvector
                └ vLLM       8000(생성)·8001(임베딩) — Docker 밖, 원래 서버에 있던 것. 건드리지 않음
학과 홈페이지(워드프레스) ── <script src="https://aihub.yju.ac.kr/widget.js"> ──▶ 위젯
```

> ⚠️ **같이 쓰는 서버**입니다. vLLM과 다른 사용자의 프로그램은 멈추거나 지우지 마세요. `docker system prune`처럼 **다른 사람 것까지 지우는 명령은 쓰지 않습니다.**
> 우리 컨테이너는 이름이 `rag_`로 시작하고, `docker compose` 명령은 프로젝트 폴더 안에서만 우리 것을 다룹니다.

---

## 1. 서버 준비 (처음 한 번, sudo 필요)
서버 관리자에게 미리 말씀드리고 진행.
```bash
docker compose version || sudo apt install docker-compose-v2   # 컨테이너 여러 개를 한 번에 띄우는 도구
sudo usermod -aG docker $USER                                   # 내 계정이 Docker를 쓸 수 있게 → 접속을 끊었다 다시 접속해야 적용
```
- docker 그룹은 사실상 관리자 권한과 같음(컨테이너로 서버 파일에 접근 가능) — 꼭 필요한 계정만.
- 확인: 다시 접속한 뒤 `docker ps`가 `permission denied` 없이 나오면 됨.

## 2. 코드 받기 + `.env` 작성
```bash
git clone https://github.com/Seo-JeongJin/gsc-agentic-rag-chatbot.git
cd gsc-agentic-rag-chatbot
cp .env.example .env
nano .env
```
`.env`에 넣을 값(설명은 `.env.example` 주석):
| 항목 | 값 |
|---|---|
| `POSTGRES_PASSWORD` | **새로 만든 긴 무작위 값** — `openssl rand -hex 24`로 생성(숫자·알파벳만). 학과 PC 값을 쓰지 말 것 |
| `LLM_MODE` / `EMBEDDING_MODE` | `vllm` / `vllm` |
| `VLLM_URL` | `http://host.docker.internal:8000` (같은 서버의 vLLM — 안 되면 "문제가 생기면" 참고) |
| `VLLM_MODEL` | `curl http://localhost:8000/v1/models`의 `id` |
| `EMBEDDING_URL` | `http://host.docker.internal:8001` |
| `EMBEDDING_MODEL` | `curl http://localhost:8001/v1/models`의 `id` |
| `CORS_ALLOW_ORIGINS` | `https://gsc.yju.ac.kr` (위젯을 붙일 학과 홈페이지만 허용) |
| `NGINX_CONF` | **지금은 적지 않음** — 6단계에서 https 인증서를 받은 뒤 추가 |

`.env`는 git에 올라가지 않음(비밀번호가 있음). 다른 사람이 못 읽게: `chmod 600 .env`
- 비밀번호를 `hex`로 만드는 이유: DB 접속 주소(`postgresql://rag:<비밀번호>@db…`) 안에 들어가서, `base64`처럼 `/`·`+`가 섞이면 주소가 깨질 수 있음.
- 사람이 입력할 일은 없음(컨테이너끼리 씀). **처음 실행할 때 DB가 이 값으로 만들어지므로 나중에 바꾸지 말 것**(바꾸면 접속이 안 됨).
- 화면에 비밀번호를 띄우지 않고 만드는 예: `.env`의 그 줄을 `POSTGRES_PASSWORD=$(openssl rand -hex 24)`로 쓰고 `cat > .env <<EOF … EOF`로 생성.

## 3. FAQ 문서 복사
검색 대상 문서는 git에 없음. **학과 PC에서** 서버로 복사:
```bash
scp data/<FAQ파일>.md <계정>@aihub.yju.ac.kr:~/gsc-agentic-rag-chatbot/data/
```

## 4. 실행 — 챗봇 3개만 먼저 (nginx는 6단계에서)
```bash
cp deploy/nginx/admin-allow.conf.example deploy/nginx/admin-allow.conf   # 관리자 영역 허용 IP 파일(git 제외)
docker compose up -d --build                      # --profile deploy 없이 → db·mcp_server·backend 3개만. 처음엔 빌드 5~15분
docker compose ps                                 # 3개가 Up인지(db·mcp_server는 healthy까지)
```
- **nginx를 아직 안 띄우는 이유**: nginx가 뜨는 순간 80번으로 외부에 열림. https와 학교 IP 제한(6·7단계)을 하기 전에 열리지 않게,
  확인하는 동안은 챗봇을 서버 안(127.0.0.1:8080)에서만 접속되게 둠.
- 원래 서버에 있던 컨테이너(vLLM 등)는 그대로인지도 확인: `docker ps`
**vLLM 접속 확인**(가장 먼저 — 안 되면 챗봇이 답을 못 함):
```bash
docker compose exec backend python3 -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8000/v1/models', timeout=10).status)"
docker compose exec mcp_server python3 -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:8001/v1/models', timeout=10).status)"
```
둘 다 `200`이면 됨.

## 5. 문서 색인 + 관리자 계정
```bash
docker compose exec mcp_server python3 index_cli.py /data/<FAQ파일>.md
docker compose exec backend python3 create_admin_cli.py <아이디> '<10자 이상 비밀번호>'
```
- 서버의 DB는 새로 만들어지므로 학과 PC의 관리자 계정·질문 기록은 넘어오지 않음(대시보드가 실제 기록만으로 시작).
- 비밀번호는 10자 미만이면 거부됨. 특수문자가 있으면 작은따옴표로 감쌀 것.

## 6. https 인증서
**먼저 관리자 영역을 전부 잠금** — 아래 스크립트가 nginx를 띄우는 순간 외부에 열리므로, 학교 IP를 확인하기(7단계) 전까지 대시보드가 밖에서 보이지 않게.
위젯 파일·질문·인증서 확인 경로는 이 목록과 상관없이 열려 있어서 발급에는 영향 없음.
```bash
printf '# 임시: 학교 IP 확인 전까지 관리자 영역 전부 차단
deny all;
' > deploy/nginx/admin-allow.conf
```
```bash
./deploy/init-https.sh <인증서 만료 알림 받을 이메일> --staging   # ① 연습 발급 — 외부에서 80번 확인이 되는지
./deploy/init-https.sh <이메일>                                   # ② 진짜 발급
```
② 성공 후:
```bash
echo "NGINX_CONF=nginx.https.conf" >> .env
docker compose --profile deploy up -d nginx certbot
```
확인(서버에서):
```bash
docker compose --profile deploy ps                                   # 5개 Up
echo | openssl s_client -connect aihub.yju.ac.kr:443 -servername aihub.yju.ac.kr 2>/dev/null | openssl x509 -noout -issuer -enddate   # Let's Encrypt, STAGING 없음
curl -s -o /dev/null -w '%{http_code}
' https://aihub.yju.ac.kr/widget.js   # 200
curl -s -o /dev/null -w '%{http_code}
' https://aihub.yju.ac.kr/            # 403(아직 잠금)
```
- 연습부터 하는 이유: Let's Encrypt 진짜 서버는 실패를 반복하면 한동안 발급을 막음.
- 인증서는 90일짜리지만 certbot 컨테이너가 자동 갱신(하루 두 번 확인), nginx는 6시간마다 새 인증서를 다시 읽음.

## 7. 관리자 영역을 학교 IP만으로
대시보드(`/`)·`/admin/`·`/docs`·위젯 시험 페이지(`/widget-test/`)는 허용한 IP에서만(위젯 파일 `/widget.js`·질문 `/api/`는 누구나).
1. **허용할 학교 PC**에서 표시를 붙여 접속(막혀서 403이 나도 IP는 기록에 남음):
   ```bash
   curl -s -o /dev/null -A "my-pc-check" https://aihub.yju.ac.kr/     # 또는 브라우저로 https://aihub.yju.ac.kr/ 접속
   ```
2. 서버에서 그 접속이 어떤 IP로 들어왔는지 확인 — 맨 앞 숫자:
   ```bash
   docker compose --profile deploy logs nginx | grep my-pc-check | awk '{print $3}'
   ```
   학교 PC는 보통 **내부 IP(`10.x.x.x`)**로 보임(학교 안쪽 망이라 공인 주소로 바뀌지 않고 그대로 옴).
3. `deploy/nginx/admin-allow.conf`(git 제외 파일)에 **확인된 IP만** 적음:
   ```
   allow <2번에서 본 IP>;
   deny  all;
   ```
   학교 주소 범위 전체(`10.0.0.0/8` 등)를 여는 건 학교 네트워크 구성을 확인한 뒤에만. 다른 PC는 403이 나면 같은 방법으로 한 줄씩 추가.
4. 적용(문법 검사가 통과해야만 적용됨, 접속은 안 끊김):
   ```bash
   docker compose --profile deploy exec nginx nginx -t && docker compose --profile deploy exec nginx nginx -s reload
   ```

## 8. 외부에서 확인 (휴대폰, 와이파이 끄고 LTE)
| 확인 | 기대 |
|---|---|
| `https://aihub.yju.ac.kr/widget.js` | 위젯 코드 글자가 보임(열림 — 홈페이지가 불러가는 파일) |
| `https://aihub.yju.ac.kr/` (대시보드) | **403** (학교 밖이라 막힘) |
| `https://aihub.yju.ac.kr/widget-test/` (시험 페이지) | **403** (허용 IP 전용) |
| `http://aihub.yju.ac.kr/widget.js` | `https://`로 자동 이동 |
| 허용한 학교 PC에서 `https://aihub.yju.ac.kr/` | 대시보드 로그인 화면 → 로그인 |
| 허용한 학교 PC에서 `https://aihub.yju.ac.kr/widget-test/` → 위젯에서 질문 | 답이 옴 |
- 아이폰 사파리는 정상 https에 자물쇠를 따로 안 보여줌 — "안전하지 않음" 경고가 없으면 정상.
- **시험 질문도 실제 질문처럼 DB에 저장됨** → 정식 오픈 전에 지움:
  ```bash
  docker compose exec -T db psql -U rag -d rag -c "SELECT id, session_id, left(question_text,30), created_at FROM questions ORDER BY id;"
  docker compose exec -T db psql -U rag -d rag -c "DELETE FROM questions WHERE session_id='<세션>'; DELETE FROM sessions WHERE session_id='<세션>';"
  ```

## 9. 학과 홈페이지에 위젯 붙이기
워드프레스 관리자 화면(Elementor → Custom Code 등)에서 **모든 페이지의 body 끝**에 이 한 줄:
```html
<script src="https://aihub.yju.ac.kr/widget.js"></script>
```
- 위젯이 이 파일을 불러온 주소(`aihub.yju.ac.kr`)로 질문을 보냄 — 다른 설정 필요 없음.
- 위젯을 고쳐 다시 배포해도 이 줄은 그대로(브라우저가 매번 바뀌었는지 확인함).
- 내리기: Custom Code를 비활성화하면 홈페이지는 원래대로.

---

## 10. 업데이트 (코드가 바뀐 뒤)
학과 PC에서 커밋·push한 뒤, 서버에서:
```bash
cd ~/gsc-agentic-rag-chatbot && ./deploy/deploy.sh
```
사전 확인 → `git pull` → 다시 빌드·교체 → 자동 확인(컨테이너 5개, nginx 설정, 위젯 파일, vLLM 접속) → 추가로 할 일 안내
(문서 색인 방식 변경 시 재색인, `schema.sql` 변경 시 직접 적용, `.env.example`에 새 설정).
- 서버에서 git 관리 파일을 직접 고쳐 두면 멈춤 — 고칠 건 학과 PC에서 커밋·push.
- 확인 중 실제 질문은 보내지 않음(대시보드 통계에 가짜 질문이 섞이지 않게).

## 11. 멈추기 · 되돌리기
| 하고 싶은 것 | 명령 |
|---|---|
| 전부 멈추기(데이터는 남음) | `docker compose --profile deploy down` |
| 다시 켜기 | `docker compose --profile deploy up -d` |
| 이전 버전으로 | `git checkout <이전 커밋>` → `docker compose --profile deploy up -d --build` (고친 뒤 `git checkout main`) |
| https만 끄고 http로 | `.env`의 `NGINX_CONF` 줄 삭제 → `docker compose --profile deploy up -d nginx` |
| 홈페이지 위젯만 내리기 | 워드프레스에서 Custom Code 비활성화 |

⚠️ `docker compose down -v`는 **DB와 인증서까지 지움**(질문 기록 전부 삭제, 인증서 재발급 필요) — 쓰지 말 것.

## 12. 문제가 생기면
| 증상 | 확인 / 해결 |
|---|---|
| 4단계 vLLM 확인이 실패 | 서버 방화벽(ufw)이 컨테이너→서버 연결을 막을 수 있음 → `.env`의 `VLLM_URL`·`EMBEDDING_URL`을 서버 공인 주소(`http://<서버 IP>:8000`·`8001`)로 바꾸고 `docker compose --profile deploy up -d`, 다시 확인 |
| 챗봇이 "답변 서버가 잠시 응답하지 않습니다" | vLLM이 꺼졌거나 바쁨 — `curl http://localhost:8000/v1/models`(서버 관리자에게 문의) |
| 6단계 발급 실패 | 외부에서 80번이 열려 있는지, `aihub.yju.ac.kr`이 이 서버를 가리키는지(`nslookup aihub.yju.ac.kr`), `docker compose --profile deploy logs nginx` |
| 학교에서도 대시보드 403 | 7단계 IP가 틀림 — 로그로 다시 확인. 급하면 `admin-allow.conf`를 `allow all;`로 되돌리고 reload |
| 관리자 로그인이 "시도가 너무 많습니다" | 같은 IP에서 15분 안에 5번 틀림 — 15분 기다림 |
| 컨테이너가 계속 재시작 | `docker compose --profile deploy logs <서비스> --tail 50` |

## 운영 메모
- 로그: `docker compose --profile deploy logs -f backend` (질문 분류·출력 검토 결과가 보임)
- DB 백업: `docker compose exec db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup_$(date +%F).sql`
