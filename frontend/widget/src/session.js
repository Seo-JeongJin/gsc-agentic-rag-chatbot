/**
 * 세션 관리 — session_id를 쿠키로 생성/보관.
 *
 * 오래 유지되는 쿠키로 만든 이유: "재방문자 추적"이 의미가 있으려면 브라우저를 닫았다 켜도
 * 같은 세션으로 인식돼야 하기 때문 (backend/db/session_service.py의 is_returning 로직과 연결됨).
 * 대화 내용 자체의 유지기간(24시간)은 session_id가 아니라 서버의 conversation_reset_at으로 따로 관리됨.
 */

const SESSION_COOKIE_NAME = "gsc_chat_session_id";
const SESSION_COOKIE_MAX_AGE_DAYS = 365;

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

function setCookie(name, value, days) {
  const maxAgeSeconds = days * 24 * 60 * 60;
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=${maxAgeSeconds}; path=/; SameSite=Lax`;
}

export function getOrCreateSessionId() {
  let sessionId = getCookie(SESSION_COOKIE_NAME);
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    setCookie(SESSION_COOKIE_NAME, sessionId, SESSION_COOKIE_MAX_AGE_DAYS);
  }
  return sessionId;
}
