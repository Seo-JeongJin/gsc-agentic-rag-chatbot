import { useEffect, useState } from "react";
import { analyze, getLatestInsight } from "./api.js";
import CategorySuggestions from "./CategorySuggestions.jsx";

export default function Analysis() {
  const [insight, setInsight] = useState(null);
  const [checked, setChecked] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getLatestInsight()
      .then(setInsight)
      .finally(() => setChecked(true));
  }, []);

  const handleAnalyze = async () => {
    setLoading(true);
    try {
      setInsight(await analyze());
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button className="btn btn-primary" onClick={handleAnalyze} disabled={loading}>
        {loading ? "분석 중..." : "로그 분석하기"}
      </button>

      {!checked ? null : insight ? (
        <div className="insight-section">
          <p className="insight-summary">{insight.content.summary}</p>
          <p className="insight-meta">
            전체 {insight.content.based_on_count}건 중 신규 {insight.content.new_since_last_count}건 반영
            {insight.cached ? " — 신규 데이터 없어 재분석 생략, 기존 결과" : ""}
          </p>
          <p className="insight-meta">{new Date(insight.generated_at).toLocaleString("ko-KR")}</p>
        </div>
      ) : (
        <p className="muted" style={{ marginTop: 20 }}>
          아직 분석한 적이 없습니다.
        </p>
      )}

      <CategorySuggestions />
    </div>
  );
}
