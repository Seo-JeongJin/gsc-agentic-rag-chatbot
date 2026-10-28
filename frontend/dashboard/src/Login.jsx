import { useState } from "react";
import { login } from "./api.js";

export default function Login({ onSuccess }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      onSuccess();
    } catch {
      setError("아이디 또는 비밀번호가 올바르지 않습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="shell-center">
      <h1 className="login-title">
        <span style={{ color: "var(--color-brand)" }}>GSC</span> 관리자 로그인
      </h1>
      <form onSubmit={handleSubmit}>
        <input
          className="login-field"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="아이디"
        />
        <input
          className="login-field"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호"
        />
        {error && <p className="error-text">{error}</p>}
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "로그인 중..." : "로그인"}
        </button>
      </form>
    </div>
  );
}
