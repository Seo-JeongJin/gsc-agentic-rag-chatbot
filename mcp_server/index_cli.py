"""
문서 색인 실행 명령 — 학과 문서(마크다운) 파일 경로를 주면 청크로 쪼개서 documents 테이블에 색인.

새 컴퓨터(학과 컴퓨터 등)에서 DB를 처음 채울 때, 그리고 문서 수정·임베딩 모델 교체 후 다시 색인할 때 사용.
같은 source(기본값: 파일 이름)로 다시 실행하면 그 문서의 기존 청크를 지우고 새로 넣음(indexing_pipeline.py 참고).

사용법:
    # Docker로 띄운 경우 — 프로젝트 루트의 data/ 폴더가 컨테이너의 /data로 연결돼 있음(docker-compose.yml)
    docker compose exec mcp_server python3 index_cli.py /data/gsc-source-FAQ-updated.md

    # 로컬에서 직접 실행하는 경우 (mcp_server/ 폴더에서)
    python3 index_cli.py ../data/gsc-source-FAQ-updated.md --source gsc-source-FAQ-updated.md
"""

import argparse
import asyncio
from pathlib import Path

import db
from indexing_pipeline import index_document


async def run(argv: list[str] | None = None) -> int:
    """명령줄 인자를 해석해서 색인을 실행하고, 저장된 청크 수를 반환. 테스트에서도 이 함수를 직접 호출함."""
    parser = argparse.ArgumentParser(description="학과 문서(마크다운)를 documents 테이블에 색인")
    parser.add_argument("path", help="색인할 마크다운 파일 경로")
    parser.add_argument(
        "--source",
        help="문서 구분 이름(기본값: 파일 이름). 같은 이름으로 다시 색인하면 기존 청크가 교체됨",
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.is_file():
        parser.error(f"파일을 찾을 수 없음: {path}")
    source = args.source or path.name

    count = await index_document(source, path.read_text(encoding="utf-8"))
    print(f"[index_cli] '{source}' 색인 완료 — 청크 {count}개 저장")
    return count


async def _main() -> None:
    """명령줄 실행 진입점 — 색인 후 DB 연결 풀을 닫고 끝냄(안 닫으면 프로세스 종료 시 경고가 남음).
    풀이 실제로 만들어졌을 때만 닫음 — 파일 경로가 틀려서 DB에 접속하기 전에 끝난 경우, 닫으려고 새로
    접속하다가(DB도 꺼져 있으면) 엉뚱한 접속 에러가 원래의 "파일 없음" 메시지를 가리는 걸 막기 위함."""
    try:
        await run()
    finally:
        if db._pool is not None:
            await db._pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
