/**
 * UI 빌드 — Shadow DOM 안에 버튼/패널/메시지창을 그려넣고, 상태(열림/닫힘/로딩)를 관리.
 *
 * Shadow DOM을 쓰는 이유: 학과 홈페이지(워드프레스 테마의 CSS)와 위젯 스타일이 서로
 * 충돌하지 않게 완전히 격리하기 위함 — 그래서 CSS를 외부 링크가 아니라 shadow root 안에
 * <style> 태그로 직접 주입함.
 */

const STYLES = `
  :host{all:initial;}
  *{box-sizing:border-box;}
  .wrap{
    font-family:"Noto Sans KR",system-ui,sans-serif;
    --primary:#1D5FD6;
    --primary-light:#5CA8FF;
    --navy:#0B1626;
    --muted:#5B6472;
    --accent:#FF8A00;
    --bg-panel:#FFFFFF;
    --bg-bot-bubble:#F0F4FA;
    --bg-input:#F5F7FA;
    --border:#E3E8F0;
    --text:#0B1626;
    --ease-bounce:cubic-bezier(.34,1.56,.64,1);
  }

  .fab-wrap{position:fixed;right:20px;bottom:20px;z-index:2147483000;display:flex;align-items:flex-end;gap:10px;}
  .fab-tooltip{background:var(--navy);color:#fff;font-size:12.5px;font-weight:500;padding:8px 13px;border-radius:10px;white-space:nowrap;box-shadow:0 6px 18px rgba(11,22,38,.25);transform:translateY(4px);opacity:0;transition:opacity .2s,transform .2s;}
  .fab-wrap.show-tip .fab-tooltip{opacity:1;transform:translateY(0);}
  .fab{width:58px;height:58px;border-radius:50%;background:var(--accent);border:none;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 10px 24px rgba(255,138,0,.38);transition:transform .3s var(--ease-bounce),box-shadow .2s ease;flex-shrink:0;}
  .fab:hover{transform:translateY(-3px) scale(1.08);box-shadow:0 14px 30px rgba(255,138,0,.46);}
  .fab:active{transform:scale(.88);transition:transform .08s ease;}
  .fab svg{width:26px;height:26px;}
  .fab .icon-close{display:none;}
  .fab.open .icon-chat{display:none;}
  .fab.open .icon-close{display:block;}

  .panel{position:fixed;right:20px;bottom:90px;width:380px;max-width:calc(100vw - 32px);height:min(600px,calc(100vh - 140px));background:var(--bg-panel);border-radius:24px;box-shadow:0 24px 50px rgba(29,95,214,.18),0 2px 6px rgba(11,22,38,.08);display:flex;flex-direction:column;overflow:hidden;z-index:2147483000;opacity:0;transform:translateY(24px) scale(.9);pointer-events:none;transition:opacity .28s ease,transform .4s var(--ease-bounce);}
  .panel.open{opacity:1;transform:translateY(0) scale(1);pointer-events:auto;}

  .panel-header{background:linear-gradient(135deg,var(--primary),#1646a8);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;flex-shrink:0;}
  .panel-avatar{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.16);display:flex;align-items:center;justify-content:center;flex-shrink:0;overflow:hidden;}
  .panel-avatar svg{width:18px;height:18px;}
  .panel-avatar img{width:100%;height:100%;object-fit:cover;}
  .panel-title{flex:1;min-width:0;}
  .panel-title .name{font-weight:700;font-size:13.5px;line-height:1.3;}
  .panel-title .status{font-size:11.5px;color:#CFE0FF;}
  .icon-btn{width:30px;height:30px;border:none;background:rgba(255,255,255,.12);border-radius:10px;color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s,transform .25s var(--ease-bounce);flex-shrink:0;}
  .icon-btn:hover{background:rgba(255,255,255,.24);transform:scale(1.1);}
  .icon-btn:active{transform:scale(.85);}
  .icon-btn svg{width:16px;height:16px;}

  .messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px;background:var(--bg-panel);}
  .msg-row{display:flex;gap:8px;max-width:92%;}
  .msg-row.bot{align-self:flex-start;}
  .msg-row.user{align-self:flex-end;flex-direction:row-reverse;}
  .msg-avatar{width:26px;height:26px;border-radius:50%;background:var(--primary);display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:2px;overflow:hidden;}
  .msg-avatar svg{width:14px;height:14px;}
  .msg-avatar img{width:100%;height:100%;object-fit:cover;}
  .bubble{padding:10px 14px;border-radius:18px;font-size:13.5px;line-height:1.55;animation:pop-in .32s var(--ease-bounce);white-space:pre-wrap;}
  @keyframes pop-in{from{opacity:0;transform:scale(.8) translateY(6px);}to{opacity:1;transform:scale(1) translateY(0);}}
  .msg-row.bot .bubble{background:var(--bg-bot-bubble);color:var(--text);border-top-left-radius:4px;}
  .msg-row.user .bubble{background:var(--primary);color:#fff;border-top-right-radius:4px;}
  .msg-meta{display:flex;align-items:center;gap:10px;margin:5px 2px 0;font-size:11px;color:var(--muted);}
  .rate-btns{display:flex;gap:4px;margin-left:auto;}
  .rate-btn{width:22px;height:22px;border:1px solid var(--border);background:var(--bg-panel);border-radius:8px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:var(--muted);transition:background .15s,color .15s,border-color .15s,transform .3s var(--ease-bounce);}
  .rate-btn svg{width:11px;height:11px;}
  .rate-btn:hover{border-color:var(--primary-light);color:var(--primary);transform:scale(1.15);}
  .rate-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;transform:scale(1.15);}

  .typing{display:flex;gap:4px;align-items:center;padding:12px 13px;}
  .typing span{width:6px;height:6px;border-radius:50%;background:var(--muted);opacity:.5;animation:bounce 1.1s infinite;}
  .typing span:nth-child(2){animation-delay:.15s;}
  .typing span:nth-child(3){animation-delay:.3s;}
  @keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.4;}30%{transform:translateY(-4px);opacity:1;}}

  .composer{display:flex;align-items:center;gap:8px;padding:12px;border-top:1px solid var(--border);background:var(--bg-panel);flex-shrink:0;}
  .composer input{flex:1;border:1px solid var(--border);background:var(--bg-input);border-radius:999px;padding:10px 15px;font-size:13.5px;color:var(--text);font-family:inherit;outline:none;min-width:0;}
  .composer input:focus{border-color:var(--primary-light);}
  .send-btn{width:38px;height:38px;border-radius:50%;border:none;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;flex-shrink:0;transition:transform .25s var(--ease-bounce),box-shadow .15s ease;box-shadow:0 4px 10px rgba(255,138,0,.35);}
  .send-btn:hover{transform:scale(1.12) rotate(-6deg);}
  .send-btn:active{transform:scale(.85);}
  .send-btn svg{width:16px;height:16px;}
  .send-btn:disabled{opacity:.5;cursor:default;transform:none;}

  @media (max-width:460px){
    .panel{right:12px;left:12px;width:auto;bottom:84px;height:min(560px,calc(100vh - 128px));}
    .fab-wrap{right:14px;bottom:14px;}
  }
`;

const BOT_AVATAR_SVG = `<svg viewBox="0 0 24 24" fill="none"><path d="M4 12a8 8 0 1 1 3.2 6.4L4 19l1.1-3.3A7.96 7.96 0 0 1 4 12Z" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/></svg>`;

const MARKUP = `
  <div class="wrap">
    <div class="panel" id="panel" role="dialog" aria-label="입학상담 챗봇">
      <div class="panel-header">
        <div class="panel-avatar" id="headerAvatar">${BOT_AVATAR_SVG}</div>
        <div class="panel-title">
          <div class="name">글로벌시스템융합과 AI 상담</div>
          <div class="status">AI 챗봇이 답변해 드려요</div>
        </div>
        <button class="icon-btn" id="resetBtn" title="새 대화 시작" aria-label="새 대화 시작">
          <svg viewBox="0 0 24 24" fill="none"><path d="M4 4v5h5M20 20v-5h-5M4.5 15a8 8 0 0 0 14.1 3.4M19.5 9A8 8 0 0 0 5.4 5.6" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="icon-btn" id="closeBtn" title="닫기" aria-label="챗봇 닫기">
          <svg viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="#fff" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div class="messages" id="messages"></div>
      <div class="composer">
        <input type="text" id="msgInput" placeholder="질문을 입력하세요" aria-label="질문 입력">
        <button class="send-btn" id="sendBtn" aria-label="전송">
          <svg viewBox="0 0 24 24" fill="none"><path d="M3 11.5 20 4l-7.5 17-2.2-7.3L3 11.5Z" stroke="#fff" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>
    <div class="fab-wrap" id="fabWrap">
      <div class="fab-tooltip">궁금한 점을 물어보세요</div>
      <button class="fab" id="fabBtn" aria-label="챗봇 열기/닫기">
        <svg class="icon-chat" viewBox="0 0 24 24" fill="none"><path d="M4 12a8 8 0 1 1 3.2 6.4L4 19l1.1-3.3A7.96 7.96 0 0 1 4 12Z" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>
        <svg class="icon-close" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></svg>
      </button>
    </div>
  </div>
`;

/**
 * shadowRoot 안에 위젯 마크업/스타일을 그려넣고, 버튼 콜백을 연결.
 * callbacks: { onSend(text), onReset(), onRate(questionId, rating) }
 * 반환값: index.js가 메시지를 렌더링할 때 쓸 함수 모음.
 */
export function mountWidget(shadowRoot, callbacks) {
  const styleEl = document.createElement("style");
  styleEl.textContent = STYLES;
  shadowRoot.appendChild(styleEl);

  const container = document.createElement("div");
  container.innerHTML = MARKUP;
  shadowRoot.appendChild(container);

  const panel = shadowRoot.getElementById("panel");
  const fab = shadowRoot.getElementById("fabBtn");
  const fabWrap = shadowRoot.getElementById("fabWrap");
  const closeBtn = shadowRoot.getElementById("closeBtn");
  const resetBtn = shadowRoot.getElementById("resetBtn");
  const messages = shadowRoot.getElementById("messages");
  const input = shadowRoot.getElementById("msgInput");
  const sendBtn = shadowRoot.getElementById("sendBtn");

  function setOpen(isOpen) {
    panel.classList.toggle("open", isOpen);
    fab.classList.toggle("open", isOpen);
    fabWrap.classList.toggle("show-tip", !isOpen);
  }

  fab.addEventListener("click", () => setOpen(!panel.classList.contains("open")));
  closeBtn.addEventListener("click", () => setOpen(false));
  setTimeout(() => { if (!panel.classList.contains("open")) fabWrap.classList.add("show-tip"); }, 600);

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function addUserMessage(text) {
    const row = document.createElement("div");
    row.className = "msg-row user";
    row.innerHTML = `<div><div class="bubble"></div></div>`;
    row.querySelector(".bubble").textContent = text;
    messages.appendChild(row);
    scrollToBottom();
  }

  // questionId가 있으면(= 실제 질문에 대한 답변) 평가버튼을 같이 그리고, 없으면(웰컴/에러 메시지)
  // 평가버튼 자체를 안 그림 — 템플릿 리터럴 안의 삼항 조건(`questionId != null ? ... : ""`)이 그 분기.
  function addBotMessage(text, questionId) {
    const row = document.createElement("div");
    row.className = "msg-row bot";
    row.innerHTML = `
      <div class="msg-avatar">${BOT_AVATAR_SVG}</div>
      <div>
        <div class="bubble"></div>
        ${questionId != null ? `
        <div class="msg-meta">
          <div class="rate-btns">
            <button class="rate-btn" data-rating="5" title="도움이 됐어요">
              <svg viewBox="0 0 24 24" fill="currentColor"><path d="M2 20h3V9H2v11Zm19-9a2 2 0 0 0-2-2h-6.1l.9-4.3.03-.32a1.5 1.5 0 0 0-.44-1.06L12.6 2 7 7.6c-.38.38-.6.9-.6 1.4v9a2 2 0 0 0 2 2h8a2 2 0 0 0 1.84-1.21l2.6-6.07c.1-.23.16-.48.16-.72v-1Z"/></svg>
            </button>
            <button class="rate-btn" data-rating="1" title="도움이 안 됐어요">
              <svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 4h-3v11h3V4ZM3 13a2 2 0 0 0 2 2h6.1l-.9 4.3-.03.32c0 .42.17.8.44 1.06l1.18 1.32L17.4 16.4c.38-.38.6-.9.6-1.4v-9a2 2 0 0 0-2-2H8a2 2 0 0 0-1.84 1.21l-2.6 6.07c-.1.23-.16.48-.16.72v1Z"/></svg>
            </button>
          </div>
        </div>` : ""}
      </div>`;
    row.querySelector(".bubble").textContent = text;

    if (questionId != null) {
      // 썸업/썸다운 두 버튼 중 하나만 선택 가능하게(라디오버튼처럼) — 누를 때마다
      // 이 메시지의 버튼 둘 다 active를 지우고, 방금 누른 것만 다시 active 처리.
      // data-rating 속성(마크업에 1 또는 5로 박아둔 값)을 그대로 백엔드 rating 값으로 전달.
      row.querySelectorAll(".rate-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          row.querySelectorAll(".rate-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          callbacks.onRate(questionId, Number(btn.dataset.rating));
        });
      });
    }

    messages.appendChild(row);
    scrollToBottom();
    return row;
  }

  function showTyping() {
    const row = document.createElement("div");
    row.className = "msg-row bot";
    row.dataset.typing = "true";
    row.innerHTML = `<div class="msg-avatar">${BOT_AVATAR_SVG}</div><div class="bubble typing"><span></span><span></span><span></span></div>`;
    messages.appendChild(row);
    scrollToBottom();
    return row;
  }

  function removeNode(node) {
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function setSending(isSending) {
    sendBtn.disabled = isSending;
    input.disabled = isSending;
  }

  function clearMessages() {
    messages.innerHTML = "";
  }

  sendBtn.addEventListener("click", () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    callbacks.onSend(text);
  });

  // isComposing 체크: 한글 입력 조합이 끝나는 시점에 Enter가 중복 발생하는 걸 방지
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing) {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      callbacks.onSend(text);
    }
  });

  resetBtn.addEventListener("click", () => callbacks.onReset());

  return { addUserMessage, addBotMessage, showTyping, removeNode, setSending, clearMessages, setOpen };
}
