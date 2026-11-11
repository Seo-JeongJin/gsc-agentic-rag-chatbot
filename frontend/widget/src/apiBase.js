/**
 * 위젯이 질문을 보낼 서버 주소 정하기 (2026-11-11 배포 0-7).
 *
 * 왜: 전엔 홈페이지에 두 줄을 넣어야 했음 — 첫 줄(window.GSC_CHAT_API_BASE = 서버 주소)을 빠뜨리거나 오타가 나면
 *     위젯은 뜨는데 질문하면 답이 안 옴. 위젯 파일을 불러온 주소(https://aihub.yju.ac.kr/widget.js)가 곧 챗봇 서버이므로,
 *     거기서 주소를 알아내면 홈페이지엔 <script src="https://aihub.yju.ac.kr/widget.js"></script> 한 줄이면 됨.
 * 우선순위: ① window.GSC_CHAT_API_BASE(적었으면 그대로 — 기존 두 줄 방식·다른 서버를 가리켜야 할 때)
 *          ② 위젯 파일을 불러온 주소의 출처(https://aihub.yju.ac.kr)
 *          ③ 둘 다 없으면 로컬 기본값(학과 PC docker compose의 8080)
 * 출처(origin)만 쓰는 이유: 챗봇 API는 서버 맨 앞(/api/...)에 있어서 위젯 파일의 폴더 경로(/widget-test/ 등)는 필요 없음.
 */

export const LOCAL_DEFAULT = "http://localhost:8080";

export function resolveApiBase(override, scriptSrc, fallback = LOCAL_DEFAULT) {
  if (override) return override.replace(/\/+$/, "");   // 끝의 "/"를 떼서 `${base}/api/ask`가 "//api"가 되지 않게
  if (scriptSrc) {
    try {
      const origin = new URL(scriptSrc).origin;
      if (origin && origin !== "null") return origin;   // file:// 로 연 경우 출처가 "null" — 서버 주소가 아니므로 무시
    } catch {
      // 주소 형식이 이상하면 무시하고 기본값으로
    }
  }
  return fallback;
}
