"""
관리자 인증 — 로그인/로그아웃/세션검증.

세션 방식(JWT 아님)을 쓰는 이유: 서버가 한 대뿐이라 JWT의 "여러 서버가 상태를 공유할 필요 없음"
장점이 의미가 없고, 사용자용 session_id 쿠키 패턴과 구조가 같아서 일관성 있음 — admin_sessions
테이블에 "이 토큰이 어느 관리자이고 언제까지 유효한지"를 저장해두고, 매 요청마다 그걸 확인한다.

비밀번호는 절대 평문으로 저장/비교하지 않음 — bcrypt로 해시된 값만 DB에 저장하고,
로그인 시에는 "입력한 비밀번호를 해시해서 저장된 해시와 같은지"가 아니라 bcrypt의
checkpw로 비교한다(해시 함수 특성상 매번 salt가 달라 단순 비교가 안 됨).
"""

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from db.connection import get_pool

SESSION_DURATION_HOURS = 24  # 관리자 로그인 세션 유지 시간 — 이 시간 지나면 자동으로 다시 로그인 필요


async def create_admin(username: str, password: str) -> None:
    """관리자 계정 생성 — 최초 계정 발급용 스크립트(create_admin_cli.py)에서만 호출.
    비밀번호는 여기서 바로 해시해서 저장, 평문은 메모리에서도 이 함수를 벗어나면 안 남음."""
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO admins (username, password_hash) VALUES ($1, $2)",
            username,
            password_hash,
        )


async def login(username: str, password: str) -> str | None:
    """
    아이디/비밀번호가 맞으면 새 세션 토큰을 발급(admin_sessions에 저장)하고 그 토큰을 반환.
    틀리면 None 반환 — 호출하는 쪽(admin_api.py의 login_endpoint)이 401로 응답하게 됨.

    주의: 아이디가 존재하지 않는 경우와 비밀번호가 틀린 경우를 구분해서 에러 메시지를 다르게 주면
    "이 아이디는 존재한다"는 정보가 새어나갈 수 있어서, 두 경우 모두 그냥 None으로 동일하게 처리.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        admin = await conn.fetchrow(
            "SELECT id, password_hash FROM admins WHERE username = $1", username
        )
        if admin is None:
            return None

        if not bcrypt.checkpw(password.encode("utf-8"), admin["password_hash"].encode("utf-8")):
            return None

        token = secrets.token_urlsafe(32)  # 추측 불가능한 랜덤 토큰 — 쿠키로 브라우저에 전달됨
        expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS)

        await conn.execute(
            "INSERT INTO admin_sessions (session_token, admin_id, expires_at) VALUES ($1, $2, $3)",
            token,
            admin["id"],
            expires_at,
        )
        return token


async def verify_session(session_token: str | None) -> int | None:
    """토큰이 유효(존재 + 만료 전)하면 admin_id를 반환, 아니면 None.
    admin_api.py의 require_admin 디펜던시가 매 /admin/* 요청마다 이걸 호출해서 인증을 확인함."""
    if not session_token:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT admin_id FROM admin_sessions WHERE session_token = $1 AND expires_at > now()",
            session_token,
        )
        return row["admin_id"] if row else None


async def logout(session_token: str) -> None:
    """로그아웃 — 해당 세션 토큰 행을 DB에서 바로 삭제(만료를 기다리지 않고 즉시 무효화)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM admin_sessions WHERE session_token = $1", session_token)
