import { useEffect, useState, type FormEvent } from "react";
import { ApiError, get, request, setIdentity, type User } from "../api";
import { Header, NOTICE } from "../App";
import { Msg } from "../ui";

export function Login() {
  const [mode, setMode] = useState<"local" | "demo" | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [demoRole, setDemoRole] = useState<"student" | "teacher">("student");
  const [demoName, setDemoName] = useState("demo-student");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    get<{ mode: "local" | "demo" }>("/api/auth/config").then((c) => setMode(c.mode))
      .catch((e) => setConfigError(e instanceof ApiError ? e.message : "无法连接服务"));
  }, []);
  useEffect(() => { setDemoName(demoRole === "teacher" ? "teacher-demo" : "demo-student"); }, [demoRole]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "local") {
        const result = await request<{ token: string; user: User }>("POST", "/api/auth/login", { username: username.trim(), password });
        setIdentity({ mode: "local", token: result.token, user: result.user });
      } else {
        const ownerId = demoName.trim();
        const ok = demoRole === "teacher" ? ownerId.startsWith("teacher-") : /^(demo|student)-/.test(ownerId);
        if (!ok) throw new ApiError(400, demoRole === "teacher" ? "演示教师身份须以 teacher- 开头" : "演示学生身份须以 demo- 或 student- 开头");
        const probe = await fetch(demoRole === "teacher" ? "/api/teacher/cases" : "/api/cases", { headers: { "X-Owner-Id": ownerId } });
        if (!probe.ok) throw new ApiError(probe.status, "演示身份仅在开发模式下可用");
        setIdentity({ mode: "demo", ownerId, user: { id: ownerId, username: ownerId, display_name: ownerId, role: demoRole } });
      }
      window.location.hash = "";
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <Header />
      <main>
        <div className="login-box">
          <div className="head"><h1>登录</h1><p className="faint" style={{ marginTop: 4 }}>{mode === "demo" ? "本机演示模式" : "使用学校分配的账号"}</p></div>
          <form onSubmit={submit}>
            {configError && <Msg tone="bad">{configError}</Msg>}
            {mode === "local" && <>
              <label className="field"><span>账号</span>
                <input className="input" style={{ height: 34 }} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required maxLength={64} autoFocus /></label>
              <label className="field"><span>密码</span>
                <input className="input" style={{ height: 34 }} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required maxLength={128} /></label>
            </>}
            {mode === "demo" && <>
              <div className="segmented" role="group" aria-label="角色">
                <button type="button" aria-pressed={demoRole === "student"} onClick={() => setDemoRole("student")}>学生</button>
                <button type="button" aria-pressed={demoRole === "teacher"} onClick={() => setDemoRole("teacher")}>教师</button>
              </div>
              <label className="field"><span>演示身份</span>
                <input className="input" value={demoName} onChange={(e) => setDemoName(e.target.value)} required maxLength={64} /></label>
            </>}
            {error && <Msg tone="bad">{error}</Msg>}
            <button className="btn btn-primary btn-lg" disabled={busy || !mode}>{busy ? "登录中…" : "登录"}</button>
            {mode === "local" && <p className="faint">忘记密码请联系任课教师重置。</p>}
          </form>
        </div>
      </main>
      <footer className="login-foot">{NOTICE}</footer>
    </div>
  );
}
