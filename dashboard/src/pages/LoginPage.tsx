import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";

export function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.login(username, password);
      navigate("/");
    } catch {
      setError("Неверный логин или пароль.");
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Pythia</h1>
        <p>Операторская консоль</p>
        <label htmlFor="username">Логин</label>
        <input id="username" value={username} onChange={(event) => setUsername(event.target.value)} />
        <label htmlFor="password">Пароль</label>
        <input
          id="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {error ? <div className="error-box">{error}</div> : null}
        <button type="submit">Войти</button>
      </form>
    </main>
  );
}
