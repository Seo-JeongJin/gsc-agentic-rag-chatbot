import { useEffect, useState } from "react";
import { getCategoryStats } from "./api.js";

function groupByMajor(rows) {
  const groups = new Map();
  for (const r of rows) {
    if (!groups.has(r.category_major)) {
      groups.set(r.category_major, { major: r.category_major, total: 0, minors: [] });
    }
    const group = groups.get(r.category_major);
    group.total += r.question_count;
    group.minors.push({ minor: r.category_minor, count: r.question_count });
  }
  const list = [...groups.values()];
  list.sort((a, b) => b.total - a.total);
  for (const g of list) g.minors.sort((a, b) => b.count - a.count);
  return list;
}

export default function Categories() {
  const [rows, setRows] = useState(null);

  useEffect(() => {
    getCategoryStats().then(setRows);
  }, []);

  if (!rows) return <p className="muted">불러오는 중...</p>;
  if (rows.length === 0) return <p className="muted">아직 분류된 질문이 없습니다.</p>;

  const groups = groupByMajor(rows);
  const maxMinorCount = Math.max(...rows.map((r) => r.question_count));

  return (
    <div>
      {groups.map((g) => (
        <div key={g.major} className="cat-group">
          <div className="cat-major-row">
            <span className="cat-major-label">{g.major}</span>
            <span className="cat-major-count">{g.total}</span>
          </div>
          {g.minors.map((m) => (
            <div key={m.minor} className="cat-row">
              <div className="cat-label">{m.minor}</div>
              <div className="cat-bar-track">
                <div
                  className="cat-bar-fill"
                  style={{ width: `${(m.count / maxMinorCount) * 100}%` }}
                />
              </div>
              <div className="cat-count">{m.count}</div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
