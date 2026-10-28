import { useEffect, useState } from "react";
import { getQuestions } from "./api.js";

const PAGE_SIZE = 20;
const FILTERS = [
  { key: "all", label: "전체" },
  { key: "true", label: "답변 못한 질문만" },
  { key: "false", label: "답변 성공만" },
];

export default function Questions() {
  const [filter, setFilter] = useState("all");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    const params = { limit: PAGE_SIZE, offset };
    if (filter !== "all") params.needs_review = filter;
    getQuestions(params)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [filter, offset]);

  return (
    <div>
      <div className="segmented" style={{ marginBottom: 20 }}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={filter === f.key ? "active" : ""}
            onClick={() => {
              setFilter(f.key);
              setOffset(0);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="muted">불러오는 중...</p>
      ) : rows.length === 0 ? (
        <p className="muted">해당하는 질문이 없습니다.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>질문</th>
              <th>답변</th>
              <th>카테고리</th>
              <th>상태</th>
              <th>평점</th>
              <th>시각</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.question_text}</td>
                <td>{r.answer_text}</td>
                <td className="cell-tight">
                  {r.category_major ? (
                    <>
                      {r.category_major} <span className="muted">{r.category_minor}</span>
                    </>
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
                <td className="cell-tight">
                  {r.needs_review ? (
                    <span className="pill pill-warn">검토필요</span>
                  ) : (
                    <span className="pill pill-ok">정상</span>
                  )}
                </td>
                <td className="cell-mono">{r.user_rating ?? "-"}</td>
                <td className="cell-mono">{new Date(r.created_at).toLocaleString("ko-KR")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} disabled={offset === 0}>
          이전
        </button>
        <button
          className="btn"
          onClick={() => setOffset(offset + PAGE_SIZE)}
          disabled={rows.length < PAGE_SIZE}
          style={{ marginLeft: 8 }}
        >
          다음
        </button>
      </div>
    </div>
  );
}
