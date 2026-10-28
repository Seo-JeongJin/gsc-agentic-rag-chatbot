/**
 * 진입점 — 학과 홈페이지가 <script src="widget.js">로 이 파일(번들된 결과)을 불러오면
 * 실행되는 부분. Shadow DOM을 만들고, ui.js/session.js/api.js를 연결한다.
 */

import { getOrCreateSessionId } from "./session.js";
import { askQuestion, resetConversation, rateAnswer } from "./api.js";
import { mountWidget } from "./ui.js";

function init() {
  // session_id는 앱 전체(이 위젯 인스턴스)에서 한 번만 가져오고 계속 재사용 —
  // 매 질문마다 다시 조회하면 쿠키가 바뀌었는지 매번 확인하는 낭비가 생김.
  const sessionId = getOrCreateSessionId();

  // 학과 홈페이지의 기존 DOM/CSS와 절대 충돌하지 않도록, 별도 호스트 엘리먼트를 만들고
  // 그 안에 Shadow DOM(완전히 격리된 독립 공간)을 열어서 위젯을 그 안에만 그림.
  const host = document.createElement("div");
  host.id = "gsc-chat-widget-host";
  document.body.appendChild(host);
  const shadowRoot = host.attachShadow({ mode: "open" });

  // mountWidget은 화면을 그리고, 버튼을 누르면 아래 3개 콜백(handleSend 등)을 불러줌.
  // 콜백 패턴을 쓰는 이유: ui.js는 "화면을 어떻게 그릴지"만 알고, "눌렸을 때 서버에 뭘 요청할지"는
  // 전혀 모르게 역할을 분리하기 위함 — 그래서 ui.js는 api.js를 import조차 안 함.
  const widget = mountWidget(shadowRoot, {
    onSend: handleSend,
    onReset: handleReset,
    onRate: handleRate,
  });

  // 처음 열었을 때 보여줄 웰컴 메시지 (questionId를 null로 넘기면 ui.js가 평가버튼을 안 그림 —
  // 아직 실제 질문/답변이 아니라서 평가할 대상이 없기 때문)
  widget.addBotMessage(
    "안녕하세요! 영진전문대학교 글로벌시스템융합과 입학상담 챗봇이에요 🙂 전형 일정이나 지원 자격, 교육과정, 일본 취업까지 뭐든 편하게 물어봐 주세요.",
    null
  );

  // 사용자가 질문을 보냈을 때의 전체 흐름: 화면에 즉시 내 질문 말풍선 띄우기 →
  // 입력 잠그기(중복전송 방지) → 로딩 표시 → 서버 응답 오면 로딩 지우고 답변 표시.
  async function handleSend(text) {
    widget.addUserMessage(text);
    widget.setSending(true); // 응답 기다리는 동안 입력창/전송버튼 비활성화 — 같은 질문 중복 전송 방지
    const typingRow = widget.showTyping();

    try {
      const { answerText, questionId } = await askQuestion(text, sessionId);
      widget.removeNode(typingRow); // 로딩 말풍선을 지우고 그 자리에 실제 답변을 새로 추가
      widget.addBotMessage(answerText, questionId); // questionId가 있어야 평가버튼이 "어느 질문인지" 알고 그려짐
    } catch (err) {
      // 서버가 아예 응답을 못 주는 경우(네트워크 오류 등) — 사용자에게는 친절한 안내문만 보여주고,
      // 실제 에러 내용은 console에만 남겨서 디버깅할 때 참고
      widget.removeNode(typingRow);
      widget.addBotMessage("죄송해요, 지금 답변을 가져오지 못했어요. 잠시 후 다시 시도해 주세요.", null);
      console.error("[GSC 챗봇]", err);
    } finally {
      widget.setSending(false); // 성공/실패 상관없이 다음 질문을 받을 수 있게 입력창 다시 활성화
    }
  }

  // "새 대화 시작" 버튼: 서버에 리셋을 알린 뒤(= sessions.conversation_reset_at 갱신),
  // 화면에 쌓여있던 메시지도 지우고 웰컴 메시지로 다시 시작. 서버 호출이 실패해도
  // 화면은 일단 깨끗하게 리셋해줌(사용자 입장에서 "새 대화"라는 UX는 유지).
  async function handleReset() {
    try {
      await resetConversation(sessionId);
    } catch (err) {
      console.error("[GSC 챗봇] 리셋 실패", err);
    }
    widget.clearMessages();
    widget.addBotMessage("새 대화를 시작했어요! 무엇이 궁금하신가요?", null);
  }

  // 평가(👍👎) 버튼: 서버에 전송만 하면 끝 — 화면은 ui.js가 버튼 클릭 시점에
  // 이미 active 스타일로 바꿔놨기 때문에 여기서 추가로 화면을 바꿀 필요 없음.
  // 질문할 때 쓴 것과 같은 sessionId를 같이 보내야 백엔드의 소유 확인을 통과함.
  async function handleRate(questionId, rating) {
    try {
      await rateAnswer(questionId, rating, sessionId);
    } catch (err) {
      console.error("[GSC 챗봇] 평가 전송 실패", err);
    }
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
