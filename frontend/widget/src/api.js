/**
 * 백엔드(FastAPI) 호출 — /api/ask, /api/reset_conversation, /api/rate.
 *
 * API_BASE: 운영 환경에서는 학과 서버의 실제 FastAPI 주소로 바뀜. 지금은 로컬 테스트용으로
 * window.GSC_CHAT_API_BASE를 먼저 보고, 없으면 localhost:8080(로컬 docker compose 기본값)을 씀
 * — 테스트 페이지(test.html)에서 이 값을 지정해서 쓴다.
 */

const API_BASE = window.GSC_CHAT_API_BASE || "http://localhost:8080";

export async function askQuestion(questionText, sessionId) {
  const res = await fetch(`${API_BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_text: questionText, session_id: sessionId }),
  });
  if (!res.ok) {
    throw new Error(`요청 실패 (${res.status})`);
  }
  const data = await res.json();
  return { answerText: data.answer_text, questionId: data.question_id };
  // 참고: 지금 backend/main.py는 완성된 답변 하나를 JSON으로 한 번에 돌려줌(가짜 LLM 응답 단계라
  // 스트리밍할 진짜 토큰이 없음). 실제 vLLM 연결 후 SSE 스트리밍이 추가되면, 이 함수는
  // EventSource나 스트림 리더로 토큰을 하나씩 받아 콜백으로 넘기는 방식으로 바뀔 예정.
}

export async function resetConversation(sessionId) {
  const res = await fetch(`${API_BASE}/api/reset_conversation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) {
    throw new Error(`리셋 요청 실패 (${res.status})`);
  }
}

// sessionId를 같이 보내는 이유: 백엔드가 "이 질문을 한 세션이 보낸 평가인지"(소유 확인)를
// 대조해서, 질문 번호만 바꿔가며 남의 질문 평점을 조작하는 걸 막기 위함(2026-10-28 리뷰 9번).
export async function rateAnswer(questionId, rating, sessionId) {
  const res = await fetch(`${API_BASE}/api/rate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question_id: questionId, rating, session_id: sessionId }),
  });
  if (!res.ok) {
    throw new Error(`평가 전송 실패 (${res.status})`);
  }
}
