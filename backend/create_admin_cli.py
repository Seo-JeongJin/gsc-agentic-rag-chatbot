"""
최초 관리자 계정 생성용 1회성 스크립트. admin_api.py 같은 서버 코드가 아니라,
터미널에서 직접 실행해서 admins 테이블에 계정 하나를 추가하는 용도.

사용법 (backend/ 디렉터리에서, venv 활성화 후):
    python3 create_admin_cli.py <아이디> <비밀번호>

예:
    python3 create_admin_cli.py admin "강한비밀번호123!"

비밀번호는 MIN_PASSWORD_LENGTH자 이상이어야 함(2026-11-11 배포 0-6) — 배포 서버에서 실수로 "test1234" 같은 계정을 만드는 걸 막기 위함.
"""

import asyncio
import sys

from db.admin_service import create_admin

# 관리자 비밀번호 최소 길이 — 대시보드는 학교 IP만 열리고(배포 0-5) 로그인 시도도 제한되지만(0-6), 학교 안에서의 대입까지 버티게.
# 길이만 보는 이유: 복잡도 규칙(특수문자 필수 등)보다 길이가 비밀번호 강도에 훨씬 크게 작용하고, 규칙이 단순해야 지키기 쉬움.
MIN_PASSWORD_LENGTH = 10


def password_problem(password: str) -> str | None:
    """비밀번호가 기준에 못 미치면 그 이유(문장), 괜찮으면 None. 판단을 함수로 뺀 이유: 테스트에서 계정을 실제로 만들지 않고 확인하려고."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다(지금 {len(password)}자)."
    return None


async def main():
    if len(sys.argv) != 3:
        print("사용법: python3 create_admin_cli.py <아이디> <비밀번호>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    problem = password_problem(password)
    if problem:
        print(f"계정을 만들지 않았습니다 — {problem}")
        sys.exit(1)
    await create_admin(username, password)
    print(f"관리자 계정 '{username}' 생성 완료.")


if __name__ == "__main__":
    asyncio.run(main())
