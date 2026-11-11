// 위젯이 질문을 보낼 서버 주소 정하기(apiBase.js) 테스트 — 실행: frontend/widget에서 `node --test`
import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveApiBase } from "./apiBase.js";

test("[배포 0-7] 설정이 없으면 위젯 파일을 불러온 주소로 질문 — 홈페이지엔 script 한 줄만 넣으면 됨", () => {
  assert.equal(resolveApiBase(undefined, "https://aihub.yju.ac.kr/widget.js"), "https://aihub.yju.ac.kr");
  assert.equal(resolveApiBase(undefined, "http://127.0.0.1:8080/widget-test/widget.js"), "http://127.0.0.1:8080");
});

test("window.GSC_CHAT_API_BASE를 적었으면 그 값이 우선 — 기존 두 줄 방식도 그대로 동작(끝의 / 는 떼어냄)", () => {
  assert.equal(resolveApiBase("https://other.example/", "https://aihub.yju.ac.kr/widget.js"), "https://other.example");
});

test("위젯 파일 주소를 알 수 없으면(옛 방식·파일 직접 열기) 로컬 기본값", () => {
  assert.equal(resolveApiBase(undefined, undefined), "http://localhost:8080");
  assert.equal(resolveApiBase(undefined, ""), "http://localhost:8080");
  assert.equal(resolveApiBase(undefined, "file:///C:/widget/dist/widget.js"), "http://localhost:8080");   // file:// 은 출처가 없음
});
