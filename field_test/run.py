"""
학교 실측 테스트 실행기 (2026-10-29) — questions.json의 질문을 실제 서비스(/api/ask)에 보내고 결과를 CSV로 저장.

왜 필요한가: 손으로 질문 몇 개씩 던지면 FAQ 문서의 많은 부분을 안 물어보게 되고, 같은 질문도 매번 답이 달라서
한 번 본 결과로 판단하기 어려움. 질문 종류별(A~F)로 기대값을 적어두고 같은 질문을 여러 번 돌려서 비교함.

자동으로 확인하는 것(기대값과 비교): 검색 여부, 핵심 사실 포함, "답변 못한 질문" 표시 여부, 연락처 안내, 금지 패턴.
자동으로 못 하는 것: "그럴듯하게 지어냈는지"(B7·B8 같은 것) — CSV의 answer를 사람이 읽고 manual_note 칸에 적을 것.

실행 방법(이 PC의 backend venv로, docker compose가 떠 있는 상태에서):
    PYTHONUTF8=1 backend/.venv/Scripts/python field_test/run.py --repeat 2
backend 컨테이너 안이 아니라 밖에서 도는 이유: backend 이미지에는 field_test/가 안 들어가서, 컨테이너 안에서 돌리려면
이미지를 다시 빌드해야 함. 밖에서는 HTTP로 /api/ask를 부르고(사용자와 같은 경로), 검색 횟수·검토 판정은 DB에서 읽음.

주의: 결과가 실제 DB(rag)에 쌓임 — 세션 이름을 "fieldtest-"로 시작하게 해서 나중에 대시보드 통계에서 거르거나 지울 수 있게 함.
"""

import argparse
import asyncio
import csv
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import asyncpg
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
API_URL = os.environ.get("FIELD_TEST_API", "http://localhost:8080/api/ask")
CONTACT_PATTERNS = ["053-940-5441", "053) 940-5441", "@gsc.yju", "gsc.yju"]  # 학과 연락처 안내가 들어갔는지 판단용
SESSION_PREFIX = "fieldtest"
REQUEST_TIMEOUT = 150.0  # 긴 절차 설명은 실측 7초 안팎 — LLM 재시도까지 감안해 넉넉히
REVIEW_WAIT_SECONDS = 30.0  # 백그라운드 출력 검토(생각모드, 실측 2~10초)가 끝나길 기다리는 최대 시간


def _db_url() -> str:
    """.env의 POSTGRES_* 값으로 호스트에서 접속하는 주소 — docker-compose가 5432를 호스트에 열어둠."""
    load_dotenv(ROOT.parent / ".env")
    return (f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
            f"@{os.environ.get('FIELD_TEST_DB_HOST', 'localhost')}:5432/{os.environ['POSTGRES_DB']}")


def check(expect: dict, answer: str, row: dict | None) -> dict:
    """기대값(expect)과 실제 결과를 비교해서 항목별 통과 여부를 돌려줌. 기대값이 없거나 null인 항목은 "-"(검사 안 함).

    row가 None이면 입력 가드레일에 막혀 DB 기록이 없는 경우 — 검색·표시 여부는 확인할 수 없어서 "-"."""
    result = {}

    search = expect.get("search")
    if search is None or row is None:
        result["check_search"] = "-"
    else:
        result["check_search"] = "O" if (row["tool_call_count"] > 0) == search else "X"

    missing = []
    for group in expect.get("facts", []):
        if not any(alt in answer for alt in group):
            missing.append("/".join(group))
    result["check_facts"] = "-" if not expect.get("facts") else ("O" if not missing else "X")
    result["missing_facts"] = "; ".join(missing)

    flagged = expect.get("flagged")
    if flagged is None or row is None:
        result["check_flagged"] = "-"
    else:
        result["check_flagged"] = "O" if row["needs_review"] == flagged else "X"

    if expect.get("contact"):
        result["check_contact"] = "O" if any(p in answer for p in CONTACT_PATTERNS) else "X"
    else:
        result["check_contact"] = "-"

    hits = [p for p in expect.get("forbid", []) if re.search(p, answer)]
    result["check_forbid"] = "-" if not expect.get("forbid") else ("O" if not hits else "X")
    result["forbid_hits"] = "; ".join(hits)

    checks = [result[k] for k in ("check_search", "check_facts", "check_flagged", "check_contact", "check_forbid")]
    result["auto_pass"] = "X" if "X" in checks else "O"
    return result


async def wait_for_review(conn, qid: int) -> dict:
    """출력 검토가 끝날 때까지 기다렸다가 질문 행을 읽음 (2026-10-29 — 검토가 응답 뒤 백그라운드로 옮겨짐).
    응답 직후엔 guardrail_passed가 아직 NULL이라 바로 읽으면 "답변 못한 질문" 표시 체크가 전부 틀린 것으로 나옴.
    needs_review=TRUE인데 NULL이면 루프 상한 폴백(검토 대상 아님)이라 안 기다림. 검토 호출이 실패하면 계속 NULL이라
    REVIEW_WAIT_SECONDS까지만 기다리고 그대로 읽음."""
    sql = "SELECT tool_call_count, guardrail_passed, needs_review, review_reason FROM questions WHERE id=$1"
    deadline = time.perf_counter() + REVIEW_WAIT_SECONDS
    while True:
        row = await conn.fetchrow(sql, qid)
        if row["guardrail_passed"] is not None or row["needs_review"] or time.perf_counter() > deadline:
            return row
        await asyncio.sleep(0.5)


async def ask(client: httpx.AsyncClient, question: str, session_id: str) -> tuple[dict, float]:
    """/api/ask 한 번 호출 → (응답 JSON, 걸린 초). 위젯과 같은 경로로 보내야 실제 사용자가 겪는 결과와 같음."""
    started = time.perf_counter()
    res = await client.post(API_URL, json={"question_text": question, "session_id": session_id})
    elapsed = time.perf_counter() - started
    res.raise_for_status()
    return res.json(), elapsed


async def main(repeat: int, only: set[str] | None) -> None:
    data = json.loads((ROOT / "questions.json").read_text(encoding="utf-8"))
    items = [q for q in data["questions"] if not only or q["id"] in only]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"results_{stamp}.csv"

    conn = await asyncpg.connect(_db_url())
    rows_out = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for run in range(1, repeat + 1):
            for item in items:
                # 질문마다(반복마다) 새 세션 — 앞 질문의 대화가 섞이지 않게(B4 세션 오염 재현 방지). F는 한 세션 안에서 여러 턴.
                session_id = f"{SESSION_PREFIX}-{stamp}-{item['id']}-r{run}"
                for turn in item["turns"]:
                    response, elapsed = await ask(client, turn, session_id)
                qid = response.get("question_id")
                answer = response.get("answer_text") or ""
                row = None
                if qid is not None:
                    row = await wait_for_review(conn, qid)
                record = {
                    "run": run, "id": item["id"], "category": item["category"],
                    "prior_turns": " / ".join(item["turns"][:-1]), "question": item["turns"][-1],
                    "answer": answer, "question_id": qid,
                    "tool_calls": row["tool_call_count"] if row else "",
                    "guardrail_passed": row["guardrail_passed"] if row else "입력 가드레일 차단",
                    "needs_review": row["needs_review"] if row else "",
                    "review_reason": (row["review_reason"] or "") if row else "",
                    "latency_s": round(elapsed, 2),
                    **check(item["expect"], answer, row),
                    "watch": item["expect"].get("watch", ""),
                    "manual_note": "",  # 사람이 답을 읽고 "지어냄/동조/맥락 섞음" 등을 적는 칸
                }
                rows_out.append(record)
                mark = record["auto_pass"]
                print(f"[r{run}] {item['id']:4} {mark}  도구{record['tool_calls']}  {record['latency_s']:>5}s  "
                      f"{record['missing_facts'] or ''}{(' 금지:' + record['forbid_hits']) if record['forbid_hits'] else ''}")
    await conn.close()

    # utf-8-sig: 엑셀에서 열었을 때 한글이 안 깨지게(BOM)
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
        writer.writeheader()
        writer.writerows(rows_out)

    # 종류별 요약 — 어떤 종류의 질문에서 자주 틀리는지 한눈에
    print("\n=== 종류별 자동 통과 ===")
    for category in sorted({r["category"] for r in rows_out}):
        rs = [r for r in rows_out if r["category"] == category]
        print(f"{category}: {sum(r['auto_pass'] == 'O' for r in rs)}/{len(rs)}")
    print(f"\n결과: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="학교 실측 테스트 — questions.json을 /api/ask로 보내고 CSV 저장")
    parser.add_argument("--repeat", type=int, default=2, help="질문마다 몇 번씩 돌릴지(같은 질문도 답이 매번 달라서 기본 2번)")
    parser.add_argument("--only", nargs="*", help="이 id만 돌리기(예: --only A1 B1)")
    args = parser.parse_args()
    asyncio.run(main(args.repeat, set(args.only) if args.only else None))
