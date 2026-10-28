import { useEffect, useState } from "react";
import { getMe, logout } from "./api.js";
import Login from "./Login.jsx";
import Overview from "./Overview.jsx";
import Questions from "./Questions.jsx";
import Categories from "./Categories.jsx";
import Analysis from "./Analysis.jsx";

const TABS = [
  { key: "overview", label: "개요", Component: Overview },
  { key: "questions", label: "질문 로그", Component: Questions },
  { key: "categories", label: "카테고리별 통계", Component: Categories },
  { key: "analysis", label: "로그 분석", Component: Analysis },
];

export default function App() {
  const [authed, setAuthed] = useState(null); // null=확인 중
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    getMe()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false));
  }, []);

  if (authed === null) return <p className="shell-center muted">확인 중...</p>;
  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;

  const Active = TABS.find((t) => t.key === tab).Component;

  return (
    <>
      <nav className="sidebar">
        <div className="sidebar-brand">
          <b>GSC</b> 관리자
        </div>
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`side-tab ${tab === t.key ? "active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <main className="main">
        <header className="shell-header">
          <h1 className="shell-title">입학상담 챗봇 관리자</h1>
          <button className="btn" onClick={() => logout().then(() => setAuthed(false))}>
            로그아웃
          </button>
        </header>
        <Active />
      </main>
    </>
  );
}
