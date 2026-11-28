import { useState, type FormEvent } from "react";
import { ApiError, request, setIdentity, type Identity, type User } from "../api";
import { Header, NOTICE } from "../App";
import { Msg, Panel } from "../ui";

function PasswordForm({ identity, onDone }: { identity: Identity; onDone?: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null); setDone(false);
    if (next.length < 12) return setError("新密码至少 12 个字符");
    if (next !== again) return setError("两次输入的新密码不一致");
    setBusy(true);
    try {
      const result = await request<{ token: string; user: User }>("POST", "/api/auth/password", { current_password: current, new_password: next });
      setIdentity({ ...identity, token: result.token, user: result.user });
      setCurrent(""); setNext(""); setAgain(""); setDone(true);
      onDone?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "修改失败");
    } finally { setBusy(false); }
  };
  return (
    <form className="stack" onSubmit={submit} style={{ maxWidth: 360 }}>
      <label className="field"><span>当前密码</span><input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
      <label className="field"><span>新密码</span><input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required /><small>12–128 个字符</small></label>
      <label className="field"><span>确认新密码</span><input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required /></label>
      {error && <Msg tone="bad">{error}</Msg>}
      {done && <Msg tone="ok">密码已修改，其他设备上的登录已退出。</Msg>}
      <div><button className="btn btn-primary" disabled={busy}>{busy ? "保存中…" : "修改密码"}</button></div>
    </form>
  );
}

export function Account({ identity }: { identity: Identity }) {
  const u = identity.user;
  return (
    <div className="stack-lg">
      <div className="page-title"><h1>账号设置</h1></div>
      <Panel title="基本信息">
        <dl className="kv">
          <dt>姓名</dt><dd>{u.display_name}</dd>
          <dt>账号</dt><dd className="mono">{u.username}</dd>
          <dt>角色</dt><dd>{u.role === "teacher" ? "教师" : "学生"}</dd>
          <dt>账号编号</dt><dd className="mono">{u.id}</dd>
        </dl>
      </Panel>
      <Panel title="修改密码"><PasswordForm identity={identity} /></Panel>
    </div>
  );
}

export function ForcedPasswordChange({ identity, onLogout }: { identity: Identity; onLogout: () => void }) {
  return (
    <div className="login-page">
      <Header identity={identity} onLogout={onLogout} />
      <main>
        <div className="login-box">
          <div className="head"><h1>设置新密码</h1><p className="faint" style={{ marginTop: 4 }}>首次登录或密码被重置后，须先设置自己的密码。</p></div>
          <div style={{ padding: "18px 22px 22px" }}><PasswordForm identity={identity} /></div>
        </div>
      </main>
      <footer className="login-foot">{NOTICE}</footer>
    </div>
  );
}
