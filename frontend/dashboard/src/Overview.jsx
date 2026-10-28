import { useEffect, useState } from "react";
import { getOverview } from "./api.js";

export default function Overview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getOverview().then(setData).catch(() => setError("불러오기 실패"));
  }, []);

  if (error) return <p className="error-text">{error}</p>;
  if (!data) return <p className="muted">불러오는 중...</p>;

  const cards = [
    { label: "전체 세션", value: data.total_sessions },
    { label: "재방문율", value: `${data.returning_rate_percent}%` },
    { label: "전체 질문", value: data.total_questions },
    { label: "평균 평점", value: data.average_rating ?? "-" },
    { label: "미분류 질문", value: data.unclassified_count },
    { label: "답변 못한 질문", value: data.needs_review_count },
  ];

  return (
    <div className="stat-strip">
      {cards.map((c) => (
        <div key={c.label} className="stat">
          <div className="stat-value">{c.value}</div>
          <div className="stat-label">{c.label}</div>
        </div>
      ))}
    </div>
  );
}
