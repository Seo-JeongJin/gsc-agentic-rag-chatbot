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

// 세션 ID(UUID v4) 만들기. crypto.randomUUID()를 바로 쓰지 않는 이유(2026-10-28, 2차 리뷰 3번):
// 이 함수는 브라우저 규칙상 "보안 컨텍스트"(HTTPS 또는 localhost)에서만 존재함 — 학과 컴퓨터를
// http://<IP>:8080/widget-test 처럼 HTTP+IP로 열면 undefined라서 예외가 나고 위젯이 아예 안 떴음.
// crypto.getRandomValues()는 HTTP에서도 쓸 수 있어서, randomUUID가 없으면 이걸로 같은 형식의 UUID를 직접 만듦.
export function generateSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // UUID 버전 4 표시
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // UUID 변형(variant) 표시
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getOrCreateSessionId() {
  let sessionId = getCookie(SESSION_COOKIE_NAME);
  if (!sessionId) {
    sessionId = generateSessionId();
    setCookie(SESSION_COOKIE_NAME, sessionId, SESSION_COOKIE_MAX_AGE_DAYS);
  }
  return sessionId;
}
