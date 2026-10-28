import { useEffect, useState } from "react";
import {
  analyzeCategorySuggestions,
  approveCategorySuggestion,
  getCategorySuggestions,
  rejectCategorySuggestion,
} from "./api.js";

const STATUS_MESSAGES = {
  insufficient_data: (r) =>
    `아직 "기타"로 분류된 질문이 ${r.count}건뿐입니다(최소 ${r.required}건부터 확인 가능).`,
  no_pattern: (r) => `"기타" 질문 ${r.checked_count}건을 확인했지만, 반복되는 진짜 주제는 찾지 못했습니다.`,
  already_proposed: (r) => `"${r.proposed_name}" 제안이 이미 대기 중입니다.`,
  already_category: (r) => `"${r.proposed_name}"는 이미 승인되어 사용 중인 카테고리입니다.`,
  proposed: () => "새 카테고리 제안이 생겼습니다 — 아래에서 확인해 주세요.",
};

export default function CategorySuggestions() {
  const [pending, setPending] = useState(null);
  const [loading, setLoading] = useState(false);
  const [resultMessage, setResultMessage] = useState(null);

  const refresh = () => getCategorySuggestions().then(setPending);

  useEffect(() => {
    refresh();
  }, []);

  const handleAnalyze = async () => {
    setLoading(true);
    setResultMessage(null);
    try {
      const result = await analyzeCategorySuggestions();
      setResultMessage(STATUS_MESSAGES[result.status]?.(result) ?? "");
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (id) => {
    await approveCategorySuggestion(id);
    await refresh();
  };

  const handleReject = async (id) => {
    await rejectCategorySuggestion(id);
    await refresh();
  };

  return (
    <div className="insight-section">
      <button className="btn" onClick={handleAnalyze} disabled={loading}>
        {loading ? "확인 중..." : "카테고리 제안 확인하기"}
      </button>
      {resultMessage && <p className="insight-meta">{resultMessage}</p>}

      <div style={{ marginTop: 16 }}>
        {pending === null ? null : pending.length === 0 ? (
          <p className="muted">대기 중인 제안 없음</p>
        ) : (
          pending.map((p) => (
            <div
              key={p.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 16,
                padding: "14px 0",
                borderBottom: "1px solid var(--color-border)",
              }}
            >
              <div>
                <strong>{p.proposed_name}</strong>
                <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                  관련 질문 {p.related_question_count}건 — {p.sample_questions?.reason}
                </div>
                {p.sample_questions?.questions?.length > 0 && (
                  <ul style={{ fontSize: 12.5, color: "var(--color-muted)", margin: "6px 0 0", paddingLeft: 18 }}>
                    {p.sample_questions.questions.slice(0, 3).map((q, i) => (
                      <li key={i}>{q}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div style={{ flexShrink: 0 }}>
                <button className="btn btn-primary" onClick={() => handleApprove(p.id)}>
                  승인
                </button>
                <button className="btn" onClick={() => handleReject(p.id)} style={{ marginLeft: 8 }}>
                  거절
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
