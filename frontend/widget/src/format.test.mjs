// 챗봇 답변 표시 형식(format.js) 테스트 — 실행: frontend/widget에서 `node --test`
// Node 내장 테스트 도구를 써서 위젯에 의존성을 추가하지 않음(위젯은 가벼워야 함).
import { test } from "node:test";
import assert from "node:assert/strict";
import { formatAnswer } from "./format.js";

test("**굵게**는 <strong>으로 바꿈 (2026-11-09 화면 확인: 답 242개 중 41개에 ** 기호가 그대로 보였음)", () => {
  assert.equal(formatAnswer("**입도선매전형**을 통해"), "<p><strong>입도선매전형</strong>을 통해</p>");
  assert.equal(formatAnswer("**A**와 **B**"), "<p><strong>A</strong>와 <strong>B</strong></p>");
});

test("HTML·스크립트는 실행되지 않게 글자로 바꿈 — 예전 textContent와 같은 XSS 방어를 유지", () => {
  assert.equal(formatAnswer("<script>alert(1)</script>"), "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>");
  assert.equal(formatAnswer('<img src=x onerror="a">'), "<p>&lt;img src=x onerror=&quot;a&quot;&gt;</p>");
});

test("굵게 안에 HTML을 넣어도 escape가 먼저라 태그가 살아나지 않음", () => {
  assert.equal(formatAnswer("**<b>x</b>**"), "<p><strong>&lt;b&gt;x&lt;/b&gt;</strong></p>");
});

test("짝이 없는 ** 나 줄을 넘는 ** 는 그대로 둠 — 엉뚱한 구간 전체가 굵어지지 않게", () => {
  assert.equal(formatAnswer("별표 ** 하나만"), "<p>별표 ** 하나만</p>");
  assert.equal(formatAnswer("**첫 줄\n둘째 줄**"), "<p>**첫 줄\n둘째 줄**</p>");
});

test("빈 줄로 나뉜 문단은 <p>로 나눔 — 문단 사이 간격을 CSS로 넓히기 위함", () => {
  assert.equal(formatAnswer("첫 문단\n\n둘째 문단\n- 목록"), "<p>첫 문단</p><p>둘째 문단\n- 목록</p>");
  assert.equal(formatAnswer("A & B\n\n\n  C"), "<p>A &amp; B</p><p>C</p>");
});

test("[사용자 요청 2026-11-09 시안 B] 한글 문장이 끝나면 다음 문장은 새 줄에서 시작 — 문장이 다 붙어 보이던 문제", () => {
  assert.equal(
    formatAnswer("다양한 학과가 있어요. 다만, 공식 문서가 정확하죠. 문의해 주세요! 감사합니다?"),
    "<p>다양한 학과가 있어요.\n다만, 공식 문서가 정확하죠.\n문의해 주세요!\n감사합니다?</p>",
  );
});

test("숫자 뒤의 점(1.0–5.9등급, 2027. 2. 10.)·쉼표에서는 줄을 안 바꿈 — 앞 글자가 한글일 때만 문장 끝으로 봄", () => {
  assert.equal(formatAnswer("평균 1.0–5.9등급 이내예요"), "<p>평균 1.0–5.9등급 이내예요</p>");
  assert.equal(formatAnswer("2027. 2. 10.에 신청해요"), "<p>2027. 2. 10.에 신청해요</p>");
  assert.equal(formatAnswer("15명 중 14명이 내정, 대부분 AI 기업"), "<p>15명 중 14명이 내정, 대부분 AI 기업</p>");
});

test("문장 뒤 이모지는 앞 문장에 붙여 둠 — 이모지 하나만 따로 한 줄이 되면 어색함", () => {
  assert.equal(formatAnswer("문의해 주세요. 😊"), "<p>문의해 주세요. 😊</p>");
});

test("굵게로 시작하는 다음 문장·괄호로 시작하는 문장도 새 줄", () => {
  assert.equal(formatAnswer("조건이 있어요. **누구나** 가능해요."), "<p>조건이 있어요.\n<strong>누구나</strong> 가능해요.</p>");
  assert.equal(formatAnswer("확인했어요. (참고) 일정이에요."), "<p>확인했어요.\n(참고) 일정이에요.</p>");
});
