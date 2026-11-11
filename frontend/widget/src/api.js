/**
 * 백엔드(FastAPI) 호출 — /api/ask, /api/reset_conversation, /api/rate.
 *
 * API_BASE: 질문을 보낼 챗봇 서버 주소 — window.GSC_CHAT_API_BASE가 있으면 그 값, 없으면 이 위젯 파일을 불러온 주소
 * (예: https://aihub.yju.ac.kr/widget.js → https://aihub.yju.ac.kr), 그것도 없으면 localhost:8080. 정하는 규칙은 apiBase.js(배포 0-7).
 */

import { resolveApiBase } from "./apiBase.js";

// document.currentScript = 지금 실행 중인 <script> 태그(= 이 위젯 파일). 파일을 처음 읽는 순간에만 값이 있어서,
// 나중(질문할 때)이 아니라 여기 맨 위에서 한 번 읽어 둠.
const API_BASE = resolveApiBase(window.GSC_CHAT_API_BASE, document.currentScript && document.currentScript.src);

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
