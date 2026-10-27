import { useState } from "react";
import { api } from "../services/api.js";

const DEMO_USERS = [
  ["demo", "demo123"],
  ["alice", "alice123"],
  ["bob", "bob123"],
];

export default function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState("demo");
  const [password, setPassword] = useState("demo123");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (u = username, p = password) => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.login(u, p);
      onLogin(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <div className="login-card">
        <div className="brand brand-lg">
          <span className="brand-mark" aria-hidden="true" />
          ShopMate
        </div>
        <p className="login-lead">
          An AI shopping agent that picks its own tools — catalog search, scoring, cart — over MCP, and asks before it
          changes anything.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <label>
            Username
            <input value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p className="error-banner" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="demo-users">
          <span className="muted small">Demo accounts (each has its own cart and conversations):</span>
          <div>
            {DEMO_USERS.map(([u, p]) => (
              <button key={u} type="button" className="btn btn-quiet" disabled={busy} onClick={() => submit(u, p)}>
                Sign in as {u}
              </button>
            ))}
          </div>
        </div>
        <p className="muted small">Local demo authentication — not production-ready.</p>
      </div>
    </main>
  );
}
