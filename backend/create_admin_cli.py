"""
최초 관리자 계정 생성용 1회성 스크립트. admin_api.py 같은 서버 코드가 아니라,
터미널에서 직접 실행해서 admins 테이블에 계정 하나를 추가하는 용도.

사용법 (backend/ 디렉터리에서, venv 활성화 후):
    python3 create_admin_cli.py <아이디> <비밀번호>

예:
    python3 create_admin_cli.py admin "강한비밀번호123!"
"""

import asyncio
import sys

from db.admin_service import create_admin


async def main():
    if len(sys.argv) != 3:
        print("사용법: python3 create_admin_cli.py <아이디> <비밀번호>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    await create_admin(username, password)
    print(f"관리자 계정 '{username}' 생성 완료.")


if __name__ == "__main__":
    asyncio.run(main())
